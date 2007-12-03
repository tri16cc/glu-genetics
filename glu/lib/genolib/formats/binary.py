# -*- coding: utf-8 -*-
'''
File:          binary.py

Authors:       Kevin Jacobs (jacobske@bioinformed.com)

Created:

Abstract:      Genotype storage formats based on a Bit-packed binary representation

Requires:      Python 2.5, glu

Revision:      $Id$
'''

from __future__ import with_statement

__copyright__ = 'Copyright (c) 2007 Science Applications International Corporation ("SAIC")'
__license__   = 'See GLU license for terms by running: glu license'

__all__ = ['BinaryGenomatrixWriter', 'BinaryGenotripleWriter',
           'save_genotriples_binary','load_genotriples_binary',
           'save_genomatrix_binary', 'load_genomatrix_binary']


from   operator                  import itemgetter
from   itertools                 import izip,groupby,imap

import tables

from   glu.lib.utils             import izip_exact, gcdisabled
from   glu.lib.fileutils         import compressed_filename

from   glu.lib.genolib.locus     import Genome, Locus
from   glu.lib.genolib.streams   import GenomatrixStream, GenotripleStream
from   glu.lib.genolib.genoarray import UnphasedMarkerModel,GenotypeArrayDescriptor,GenotypeArray


GENOMATRIX_COMPAT_VERSION,GENOMATRIX_VERSION=1,2
GENOTRIPLE_COMPAT_VERSION,GENOTRIPLE_VERSION=1,2


class TripleDesc(tables.IsDescription):
  sample = tables.Int32Col(pos=0)
  locus  = tables.Int32Col(pos=1)
  geno   = tables.Int32Col(pos=2)


CLOSED,NOTOPEN,OPEN = range(3)
STRANDS   = [None,'+','-']
STRANDMAP = dict( (s,i) for i,s in enumerate(STRANDS) )


def _get_v_attr(gfile,names,default=None):
  for name in names:
    if hasattr(gfile.root._v_attrs,name):
      return getattr(gfile.root._v_attrs,name)
  return default


class BinaryGenomatrixWriter(object):
  '''
  Object to write the genotype matrix data to a compressed binary file

  '''
  def __init__(self,filename,format,header,genome,compress=True,scratch=16*1024*1024):
    '''
    @param     filename: a file name or file object
    @type      filename: str or file object
    @param       format: text string output in the first header field to
                         indicate data format (default is blank)
    @type        format: str
    @param     compress: flag indicating if compression should be used when writing a binary genotype file.
                         Default is True.
    @type      compress: bool
    @param      scratch: the buffer space available to use while reading or writing a binary file.
    @type       scratch: int

    Example of writing an sdat file:

    >>> loci =         (    'l1',       'l2',        'l3'  )
    >>> rows = [('s1', ( ('A', 'A'), (None,None),  ('T','T'))),
    ...         ('s2', ((None,None),  ('C','T'),   ('G','T'))),
    ...         ('s3', ( ('A', 'T'),  ('T','C'),   ('G','G')))]
    >>> genos = GenomatrixStream.from_tuples(rows,'sdat',loci=loci)
    >>> import tempfile
    >>> f = tempfile.NamedTemporaryFile()
    >>> with BinaryGenomatrixWriter(f.name,genos.format,genos.loci,genos.genome) as writer:
    ...   writer.writerows(genos)
    >>> genos = load_genomatrix_binary(f.name,'sdat')
    >>> genos.format
    'sdat'
    >>> genos.loci
    ('l1', 'l2', 'l3')
    >>> for row in genos:
    ...   print row
    ('s1', [('A', 'A'), (None, None), ('T', 'T')])
    ('s2', [(None, None), ('C', 'T'), ('G', 'T')])
    ('s3', [('A', 'T'), ('C', 'T'), ('G', 'G')])

    Example of writing an ldat file:

    >>> samples =         (    's1',       's2',       's3'   )
    >>> rows    = [('l1', ( ('A', 'A'), (None,None),  ('T','T'))),
    ...            ('l2', ((None,None),  ('T','T'),   ('G','T'))),
    ...            ('l3', ( ('A', 'T'),  ('T','A'),   ('T','T')))]
    >>> genos = GenomatrixStream.from_tuples(rows,'ldat',samples=samples)
    >>> with BinaryGenomatrixWriter(f.name,genos.format,genos.samples,genos.genome) as writer:
    ...   writer.writerows(genos)
    >>> genos = load_genomatrix_binary(f.name,'ldat')
    >>> genos.format
    'ldat'
    >>> genos.samples
    ('s1', 's2', 's3')
    >>> for row in genos:
    ...   print row
    ('l1', [('A', 'A'), (None, None), ('T', 'T')])
    ('l2', [(None, None), ('T', 'T'), ('G', 'T')])
    ('l3', [('A', 'T'), ('A', 'T'), ('T', 'T')])
    '''
    if format not in ('ldat','sdat'):
      raise IOError('format must be either ldat or sdat')

    if compressed_filename(filename):
      raise IOError('Binary genotype files must not have a compressed extension')

    self.filename = filename
    self.format   = format
    self.header   = header
    self.genome   = genome

    self.scratch  = scratch
    self.state    = NOTOPEN

    if compress:
      self.filters = tables.Filters(complevel=5,complib='zlib',shuffle=(format=='sdat'),fletcher32=True)
    else:
      self.filters = tables.Filters(fletcher32=True)

  def _open(self,row1):
    self.gfile  = tables.openFile(self.filename,mode='w')

    # V1 attributes
    self.gfile.root._v_attrs.format      = self.format

    # V2 attributes
    self.gfile.root._v_attrs.GLU_FORMAT         = self.format
    self.gfile.root._v_attrs.GLU_VERSION        = GENOMATRIX_VERSION
    self.gfile.root._v_attrs.GLU_COMPAT_VERSION = GENOMATRIX_COMPAT_VERSION

    n = len(row1.data)

    crows = min(max(8, int(self.scratch//n)),8192)
    ccols = min(n,8192)

    self.genotypes = self.gfile.createEArray(self.gfile.root, 'genotypes', tables.UInt8Atom(), (0,n),
                               'Matrix of binary encoded genotypes values',
                               chunkshape=(crows,ccols), filters=self.filters, expectedrows=50000)

    if self.format == 'sdat':
      self.models = row1.descriptor.models
    elif self.format == 'ldat':
      self.models = []

    self.chunkrows = crows
    self.rowkeys   = []
    self.chunk     = []
    self.state     = OPEN

  def writerow(self, rowkey, genos):
    '''
    Write a row of genotypes given the row key and list of genotypes

    @param rowkey: row identifier
    @type  rowkey: str
    @param  genos: genotypes in an internal representation, to
                   be converted to the appropiate string representation by
                   the supplied genorepr class.
    @type   genos: sequence
    '''
    if self.state == CLOSED:
      raise IOError('Cannot write to closed writer object')
    elif self.state == NOTOPEN:
      self._open(genos)

    assert self.state == OPEN

    # FIXME: Check schema constraints!!!
    if self.format == 'ldat':
      self.models.append(genos.descriptor.models[0])

    self.rowkeys.append(rowkey)
    chunk = self.chunk
    chunk.append(genos.data)

    if len(chunk) >= self.chunkrows:
      self.genotypes.append(chunk)
      chunk[:] = []

  def writerows(self, rows):
    '''
    Write rows of genotypes given pairs of row key and list of genotypes

    @param rows: sequence of pairs of row key and sequence of genotypes in
                 an internal representation, to be converted to the
                 appropiate string representation by the supplied genorepr
                 class.
    @type  rows: sequence of (str,sequence)
    '''
    if self.state == CLOSED:
      raise IOError('Cannot write to closed writer object')
    elif self.state == NOTOPEN:
      rows = iter(rows)
      try:
        rowkey1,genos1 = rows.next()
      except StopIteration:
        return

      self._open(genos1)
      self.writerow(rowkey1,genos1)

    assert self.state == OPEN

    models  = self.models
    rowkeys = self.rowkeys
    chunk   = self.chunk

    # FIXME: Check schema constraints!!!
    if self.format == 'sdat':
      for rowkey,genos in rows:
        rowkeys.append(rowkey)
        chunk.append(genos.data)
        if len(chunk) >= self.chunkrows:
          self.genotypes.append(chunk)
          chunk[:] = []

    elif self.format == 'ldat':
      for rowkey,genos in rows:
        rowkeys.append(rowkey)
        chunk.append(genos.data)
        models.append(genos.descriptor.models[0])
        if len(chunk) >= self.chunkrows:
          self.genotypes.append(chunk)
          chunk[:] = []

  def close(self):
    '''
    Close the writer

    A closed writer cannot be used for further I/O operations and will
    result in an error if called more than once.
    '''
    if self.state == CLOSED:
      raise IOError('Writer object already closed')

    # FIXME: Figure out a way to write a valid empty genomatrix
    if self.state == NOTOPEN:
      self.state = CLOSED
      return
    assert self.state != NOTOPEN

    self.state = CLOSED
    gfile      = self.gfile
    genotypes  = self.genotypes
    self.gfile = self.genotypes = None

    if self.chunk:
      genotypes.append(self.chunk)

    self.chunk = None

    genotypes.flush()

    save_strings(gfile, 'rows', self.rowkeys, filters=self.filters)
    save_strings(gfile, 'cols', self.header,  filters=self.filters)

    if self.format == 'ldat':
      loci = self.rowkeys
    else:
      loci = self.header

    save_models(gfile, loci, self.genome, self.models, filters=self.filters)

    self.rowkeys = self.header = self.genome = self.models = None

    gfile.close()

  def __enter__(self):
    '''
    Context enter function
    '''
    return self

  def __exit__(self, *exc_info):
    '''
    Context exit function that closes the writer upon exit
    '''
    self.close()

  def __del__(self):
    '''
    Destructor to close the writer cleanly if it has yet to be closed
    '''
    if getattr(self,'state',None) == OPEN:
      self.close()


class BinaryGenotripleWriter(object):
  '''
  Object to write genotype triple data to a compressed binary file

  Triples must be suppled in the form:

    1. Sample name
    2. Locus name
    3. Genotype

  All rows output have exactly these three columns.  Sample and locus names
  are arbitrary and user-specified strings.

  >>> import tempfile
  >>> f = tempfile.NamedTemporaryFile()
  >>> triples = [('s1', 'l1', ('C','T')),
  ...            ('s1', 'l2',    (None,None)  ),
  ...            ('s1', 'l3', ('A','A')),
  ...            ('s2', 'l2', ('C','C'))]
  >>> triples = GenotripleStream.from_tuples(triples)
  >>> trips = iter(triples)
  >>> with BinaryGenotripleWriter(f.name,triples.genome) as w:
  ...   w.writerow(*trips.next())
  ...   w.writerow(*trips.next())
  ...   w.writerows(trips)
  >>> for row in load_genotriples_binary(f.name):
  ...   print row
  ('s1', 'l1', ('C', 'T'))
  ('s1', 'l2', (None, None))
  ('s1', 'l3', ('A', 'A'))
  ('s2', 'l2', ('C', 'C'))
  '''
  def __init__(self,filename,genome,compress=True,chunksize=232960):
    '''
    @param     filename: a file name or file object
    @type      filename: str or file object
    @param     compress: flag indicating if compression should be used when writing a binary genotype file.
                         Default is True.
    @type      compress: bool
    @param    chunksize: size of chunks to write/compress in bytes
    @type     chunksize: int
    '''
    if compressed_filename(filename):
      raise IOError('Binary genotype files must not have a compressed extension')

    self.genome = genome

    # Initialize self.gfile in case the next statement fails for __del__
    self.gfile = None

    self.gfile = tables.openFile(filename,mode='w')

    # V1 attributes
    self.gfile.root._v_attrs.format     = 'genotriple'

    # V2 attributes
    self.gfile.root._v_attrs.GLU_FORMAT         = 'genotriple'
    self.gfile.root._v_attrs.GLU_VERSION        = GENOTRIPLE_VERSION
    self.gfile.root._v_attrs.GLU_COMPAT_VERSION = GENOTRIPLE_COMPAT_VERSION

    if compress:
      self.filters = tables.Filters(complevel=5,complib='zlib',shuffle=True,fletcher32=True)
    else:
      self.filters = tables.Filters(fletcher32=True)

    self.genotypes = self.gfile.createTable(self.gfile.root, 'genotypes', TripleDesc,
                              'Sequence of encoded sample, locus, genotype triples',
                              filters=self.filters, chunkshape=(chunksize//4,),expectedrows=5000000)

    self.samplemap = {}
    self.locusmap  = {}
    self.modelmap  = {}

  def writerow(self, sample, locus, geno):
    '''
    Write a genotype triple (sample,locus,genotype)

    @param   sample: sample identifier
    @type    sample: str
    @param    locus: locus identifier
    @type     locus: str
    @param     geno: genotypes internal representation, to be converted to
                     the appropiate string representation by the supplied
                     genorepr class
    @type      geno: genotype representation
    '''
    if self.gfile is None:
      raise IOError('Cannot write to closed writer object')

    samplemap = self.samplemap
    locusmap  = self.locusmap
    modelmap  = self.modelmap

    locusnum =  locusmap.setdefault(locus, len(locusmap))
    if locusnum not in modelmap:
      modelmap[locusnum] = geno.model
    else:
      assert geno.model is modelmap[locusnum]

    row = self.genotypes.row
    row['sample'] = samplemap.setdefault(sample,len(samplemap))
    row['locus']  = locusnum
    row['geno']   = geno.index
    row.append()

  def writerows(self, triples):
    '''
    Write a genotype sequence of triples (sample,locus,genotype)

    @param   triples: a sequence of genotriples(str,str,genotype representation)
    @type    triples: sequence
    '''
    if self.gfile is None:
      raise IOError('Cannot write to closed writer object')

    locusmap  = self.locusmap
    modelmap  = self.modelmap
    samplemap = self.samplemap

    sd = samplemap.setdefault
    sl = samplemap.__len__
    ld = locusmap.setdefault
    ll = locusmap.__len__

    row = self.genotypes.row
    for sample,locus,geno in triples:
      locusnum      = ld(locus,ll())

      if locusnum not in modelmap:
        modelmap[locusnum] = geno.model
      else:
        assert geno.model is modelmap[locusnum]

      row['sample'] = sd(sample,sl())
      row['locus']  = locusnum
      row['geno']   = geno.index
      row.append()

  def close(self):
    '''
    Close the writer and write all of the necessary auxiliary data structures

    A closed writer cannot be used for further I/O operations and will
    result in an error if called again.
    '''
    if self.gfile is None:
      raise IOError('Writer object already closed')

    gfile      = self.gfile
    genotypes  = self.genotypes
    self.gfile = self.genotypes = None

    genotypes.flush()

    samples = map(itemgetter(0), sorted(self.samplemap.iteritems(), key=itemgetter(1)))
    loci    = map(itemgetter(0), sorted(self.locusmap.iteritems(),  key=itemgetter(1)))
    models  = map(itemgetter(1), sorted(self.modelmap.iteritems(),  key=itemgetter(0)))

    self.samplemap = self.locusmap = self.modelmap = None

    save_strings(gfile,'samples',samples,filters=self.filters)
    save_strings(gfile,'loci',   loci,   filters=self.filters)

    save_models(gfile,loci,self.genome,models,filters=self.filters)

    gfile.close()

  def __enter__(self):
    '''
    Context enter function
    '''
    return self

  def __exit__(self, *exc_info):
    '''
    Context exit function that closes the writer upon exit
    '''
    self.close()

  def __del__(self):
    '''
    Destructor to close the writer cleanly if it has yet to be closed
    '''
    if self.gfile:
      self.close()


def save_genotriples_binary(filename,triples,compress=True,chunksize=232960):
  '''
  Write the genotype triple data to file.

  @param  filename: a file name or file object
  @type   filename: str or file object
  @param   triples: a sequence of genotriples(str,str,genotype representation)
  @type    triples: sequence
  @param  compress: flag indicating if compression should be used when writing a binary genotype file.
                   Default is True.
  @type   compress: bool
  @param chunksize: size of chunks to write/compress in bytes
  @type  chunksize: int

  >>> import tempfile
  >>> f = tempfile.NamedTemporaryFile()
  >>> triples = [('s1', 'l1', ('C','T')),
  ...            ('s1', 'l2',    (None,None)  ),
  ...            ('s1', 'l3', ('A','A')),
  ...            ('s2', 'l2', ('C','C'))]
  >>> triples = GenotripleStream.from_tuples(triples)
  >>> save_genotriples_binary(f.name, triples)
  >>> for row in load_genotriples_binary(f.name):
  ...   print row
  ('s1', 'l1', ('C', 'T'))
  ('s1', 'l2', (None, None))
  ('s1', 'l3', ('A', 'A'))
  ('s2', 'l2', ('C', 'C'))
  '''
  with BinaryGenotripleWriter(filename,triples.genome,compress=compress,
                                       chunksize=chunksize) as writer:
    writer.writerows(triples)


def load_genotriples_binary(filename,unique=True,limit=None,genome=None):
  '''
  Load genotype triples from file

  @param     filename: a file name or file object
  @type      filename: str or file object
  @param       unique: flag indicating if repeated row or column elements do not exist
                       (default is True)
  @type        unique: bool
  @param        limit: limit the number of genotypes loaded
  @type         limit: int or None
  @param       genome: genome descriptor
  @type        genome: Genome instance
  @return:             sequence of tuples of sample name, locus name, and genotype representation
  @rtype:              generator

  >>> import tempfile
  >>> f = tempfile.NamedTemporaryFile()
  >>> triples = [('s1', 'l1', ('C','T')),
  ...            ('s1', 'l2',    (None,None)  ),
  ...            ('s1', 'l3', ('A','A')),
  ...            ('s2', 'l2', ('C','C'))]
  >>> triples = GenotripleStream.from_tuples(triples)
  >>> save_genotriples_binary(f.name, triples)
  >>> for row in load_genotriples_binary(f.name):
  ...   print row
  ('s1', 'l1', ('C', 'T'))
  ('s1', 'l2', (None, None))
  ('s1', 'l3', ('A', 'A'))
  ('s2', 'l2', ('C', 'C'))
  '''
  if compressed_filename(filename):
    raise IOError('Binary genotype files must not have a compressed extension')

  gfile   = tables.openFile(filename,mode='r')

  format         = _get_v_attr(gfile,['GLU_FORMAT', 'format'])
  version        = _get_v_attr(gfile,['GLU_VERSION','version'],1)
  compat_version = _get_v_attr(gfile,['GLU_COMPAT_VERSION'],version)

  if format != 'genotriple':
    raise ValueError('Unknown format: %s' % format)

  if compat_version > GENOTRIPLE_VERSION:
    raise ValueError('Unknown Genotriple file version: %s' % version)

  if version > GENOMATRIX_VERSION:
    version = compat_version

  samples = map(str,gfile.root.samples[:])
  loci    = map(str,gfile.root.loci[:])

  file_genome,models = load_models(gfile,loci,version)

  def _load():
    for row in gfile.root.genotypes:
      locusid = row[1]
      yield samples[row[0]],loci[locusid],models[locusid].genotypes[row[2]]
    gfile.close()


  # FIXME: Order must be restored
  genos = GenotripleStream(_load(),samples=set(samples),loci=set(loci),unique=unique,genome=file_genome)

  if genome:
    genos = genos.transformed(recode_models=genome)

  return genos


def save_strings(gfile,name,data,filters=None,maxlen=None):
  '''
  Save the supplied list of strings to an HDF5 table

  @param   gfile: output file
  @type    gfile: PyTables HDF5 file instance
  @param    name: output table name
  @type     name: str
  @param filters: compression and filter settings to apply
  @type  filters: PyTables HDF5 file filter instance
  @param  maxlen: maximum string length, or None to autodetect
  @type   maxlen: int or None
  '''
  if maxlen is None:
    try:
      maxlen = max(len(s) for s in data)
    except ValueError:
      maxlen = 0

  maxlen = max(maxlen,1)

  a = gfile.createCArray(gfile.root, name, tables.StringAtom(itemsize=maxlen),
                         (len(data),), filters=filters)
  a[:] = data
  a.flush()


def save_models(gfile, loci, genome, models, filters=None):
  '''
  Save the supplied list of models that correspond to specific genetic loci
  to an open HDF5 file instance

  Several tables are constructed to serialize the list of genotype models in
  order to capture all of the data elements and to minimize the total number
  of distinct objects stored.

  Tables:
    /locus_models   : n->1 mapping from locus to underlying model
    /models         : model parameters for each distinct model
    /model_genotypes: 1->n ordered mapping from model to model genotype index
    /model_alleles  : 1->n mapping from genotype index to genotype string
    /chromosomes    : lookup table of chromosome strings

  @param   gfile: output file
  @type    gfile: PyTables HDF5 file instance
  @param  models: models to save
  @type   models: list of model instances
  @param filters: compression and filter settings to apply
  @type  filters: PyTables HDF5 file filter instance
  '''
  allelemap = {}
  ad = allelemap.setdefault
  al = allelemap.__len__

  chrmap   = {}
  modelmap = {}

  #####
  # WRITE LOCUS MODELS: vector of locus -> model index
  #
  # Collect and collapse redundant models and write index array
  # also, collect an index array of alleles to write later
  class LocusModelDesc(tables.IsDescription):
    model      = tables.Int32Col(pos=0)
    chromosome = tables.Int32Col(pos=1)
    location   = tables.Int32Col(pos=2)
    strand     = tables.Int32Col(pos=3)

  locus_models = gfile.createTable(gfile.root, 'locus_models', LocusModelDesc, 'locus models',
                                       filters=filters, expectedrows=len(models))


  locus_row = locus_models.row
  for locus,model in izip_exact(loci,models):
    loc = genome.get_locus(locus)
    assert loc.model in (None,model)

    key = (model.max_alleles,model.allow_hemizygote)+tuple(g.alleles() for g in model.genotypes[1:])
    index = modelmap.get(key)
    if index is None:
      index = modelmap[key] = len(modelmap)
      for allele in model.alleles:
        ad(allele,al())

    chr = chrmap.get(loc.chromosome)
    if chr is None:
      chr = chrmap[loc.chromosome] = len(chrmap)

    locus_row['model']      = index
    locus_row['chromosome'] = chr
    locus_row['location']   = loc.location if loc.location is not None else -1
    locus_row['strand']     = STRANDMAP[loc.strand]

    locus_row.append()

  locus_models.flush()

  # Smash modelmap and chrmap down to an ordered list of tuples
  models = map(itemgetter(0), sorted(modelmap.iteritems(),  key=itemgetter(1)))
  chrs   = [ p[0] or '' for p in sorted(chrmap.iteritems(), key=itemgetter(1)) ]

  save_strings(gfile,'chromosomes',chrs,filters=filters)

  #####
  # WRITE MODELS: sequence of model max_alleles and allow_hemizygote parameters
  #
  # Used to re-construct model objects
  class ModelDesc(tables.IsDescription):
    max_alleles      = tables.UInt16Col(pos=0)
    allow_hemizygote = tables.UInt16Col(pos=1)

  mods = gfile.createTable(gfile.root, 'models', ModelDesc, 'models',
                                       filters=filters, expectedrows=len(models))

  model_row = mods.row
  for model in models:
    model_row['max_alleles']      = model[0]
    model_row['allow_hemizygote'] = model[1]
    model_row.append()

  mods.flush()

  #####
  # WRITE MODEL_GENOTYPES: model -> allele1/allele2
  #
  # Used to re-construct model objects.  Ordered list of genotypes per model
  class GenotypeDesc(tables.IsDescription):
    model   = tables.Int32Col(pos=0)
    allele1 = tables.Int32Col(pos=1)
    allele2 = tables.Int32Col(pos=2)

  genos = gfile.createTable(gfile.root, 'model_genotypes', GenotypeDesc, 'genotypes in each model',
                                        filters=filters)

  geno_row = genos.row
  for i,model in enumerate(models):
    for allele1,allele2 in model[2:]:
      geno_row['model']   = i
      geno_row['allele1'] = allelemap[allele1]
      geno_row['allele2'] = allelemap[allele2]
      geno_row.append()

  genos.flush()

  #####
  # WRITE MODEL_ALLELES: sequence of allele strings
  #
  # Used to re-construct model objects.  Ordered list of all possible alleles
  alleles = map(itemgetter(0), sorted(allelemap.iteritems(),  key=itemgetter(1)))
  alleles[0] = ''
  save_strings(gfile,'model_alleles',alleles,filters=filters)


def load_models(gfile,loci,version):
  '''
  Load models from an HDF5 binary genotype file

  Implements model compression upon input.

  @param   gfile: output file
  @type    gfile: PyTables HDF5 file instance
  '''
  if version == 1:
    return load_models_v1(gfile,loci)
  elif version == 2:
    return load_models_v2(gfile,loci)
  else:
    raise ValueError('Unknown Genotriple file version: %s' % version)


def load_models_v1(gfile,loci):
  '''
  Load models from an HDF5 binary genotype file

  Implements model compression upon input.

  @param   gfile: output file
  @type    gfile: PyTables HDF5 file instance
  '''
  assert len(gfile.root.locus_models) == len(loci)

  alleles         = map(str,gfile.root.model_alleles[:])
  alleles[0]      = None
  mods            = list(gfile.root.models[:])
  model_genotypes = dict( (i,tuple( (alleles[a1],alleles[a2]) for j,a1,a2 in mgenos) )
                           for i,mgenos in groupby(gfile.root.model_genotypes[:],itemgetter(0)) )

  modelcache = {}
  allmodels = []
  for i,mod in enumerate(mods):
    genotypes = model_genotypes.get(i,())
    key = (mod[1],mod[0])+genotypes
    model = modelcache.get(key)
    if model is None:
      model = modelcache[key] = UnphasedMarkerModel(mod[1],mod[0])
      for g in genotypes:
        model.add_genotype(g)
    allmodels.append(model)

  locus_models = [ m[0] for m in gfile.root.locus_models[:] ]
  models = [ allmodels[i] for i in locus_models ]

  # Performance hot spot (potentially millions of objects allocated here)
  with gcdisabled:
    locs = [ Locus(locus, model=model, fixed=True, chromosome=None, location=None)
                      for locus,model in izip_exact(loci,models) ]
    return Genome(loci=locs),models


def load_models_v2(gfile,loci):
  '''
  Load models from an HDF5 binary genotype file

  Implements model compression upon input.

  @param   gfile: output file
  @type    gfile: PyTables HDF5 file instance
  '''
  assert len(gfile.root.locus_models) == len(loci)

  alleles         = map(str,gfile.root.model_alleles[:])
  alleles[0]      = None
  mods            = list(gfile.root.models[:])
  chrs            = map(str,gfile.root.chromosomes[:])
  model_genotypes = dict( (i,tuple( (alleles[a1],alleles[a2]) for j,a1,a2 in mgenos) )
                           for i,mgenos in groupby(gfile.root.model_genotypes[:],itemgetter(0)) )

  modelcache = {}
  models = []
  for i,mod in enumerate(mods):
    genotypes = model_genotypes.get(i,())
    key = (mod[1],mod[0])+genotypes
    model = modelcache.get(key)
    if model is None:
      model = modelcache[key] = UnphasedMarkerModel(mod[1],mod[0])
      for g in genotypes:
        model.add_genotype(g)
    models.append(model)

  # Performance hot spot (potentially millions of objects allocated here)
  with gcdisabled:
    locs = []
    for locus,lmod in izip_exact(loci,gfile.root.locus_models[:]):
      location = lmod[2]
      if location == -1:
        location = None

      locs.append( Locus(locus, model=models[lmod[0]], fixed=True,
                                chromosome=chrs[lmod[1]], location=location,
                                strand=STRANDS[lmod[3]] ) )

    models = [ locus.model for locus in locs ]

    return Genome(loci=locs),models


def save_genomatrix_binary(filename,genos,compress=True,scratch=16*1024*1024):
  '''
  Write the genotype matrix data to file.

  @param     filename: a file name or file object
  @type      filename: str or file object
  @param        genos: genomatrix/genotriple stream
  @type         genos: sequence
  @param     compress: flag indicating if compression should be used when writing a binary genotype file.
                       Default is True.
  @type      compress: bool
  @param      scratch: the buffer space available to use while reading or writing a binary file.
  @type       scratch: int

  Example of writing an sdat file:

  >>> loci =           (  'l1',       'l2',        'l3'  )
  >>> rows = [('s1', ( ('A', 'A'), (None,None), ('T','T'))),
  ...         ('s2', ((None,None),  ('C','T'),  ('G','T'))),
  ...         ('s3', ( ('A', 'T'), ('T','C'),   ('G','G')))]
  >>> genos = GenomatrixStream.from_tuples(rows,'sdat',loci=loci)
  >>> import tempfile
  >>> f = tempfile.NamedTemporaryFile()
  >>> save_genomatrix_binary(f.name,genos)
  >>> genos = load_genomatrix_binary(f.name,'sdat')
  >>> genos.format
  'sdat'
  >>> genos.loci
  ('l1', 'l2', 'l3')
  >>> for row in genos:
  ...   print row
  ('s1', [('A', 'A'), (None, None), ('T', 'T')])
  ('s2', [(None, None), ('C', 'T'), ('G', 'T')])
  ('s3', [('A', 'T'), ('C', 'T'), ('G', 'G')])

  Example of writing an ldat file:

  >>> samples =           (  's1',      's2',        's3'  )
  >>> rows    = [('l1', ( ('A', 'A'), (None,None), ('T','T'))),
  ...            ('l2', ((None, None),('T','T'),   ('G','T'))),
  ...            ('l3', ( ('A', 'T'), ('T','A'),   ('T','T')))]
  >>> genos = GenomatrixStream.from_tuples(rows,'ldat',samples=samples)
  >>> f = tempfile.NamedTemporaryFile()
  >>> save_genomatrix_binary(f.name,genos)
  >>> genos = load_genomatrix_binary(f.name,'ldat')
  >>> genos.samples
  ('s1', 's2', 's3')
  >>> genos.format
  'ldat'
  >>> for row in genos:
  ...   print row
  ('l1', [('A', 'A'), (None, None), ('T', 'T')])
  ('l2', [(None, None), ('T', 'T'), ('G', 'T')])
  ('l3', [('A', 'T'), ('A', 'T'), ('T', 'T')])
  '''
  with BinaryGenomatrixWriter(filename,genos.format,genos.columns,genos.genome,
                                       compress=compress,scratch=scratch) as writer:
    writer.writerows(genos.transformed(repack=True))


def load_genomatrix_binary(filename,format,limit=None,unique=True,genome=None,chunksize=4096,scratch=32*1024*1024):
  '''
  Load the genotype matrix data from file.
  Note that the first row is header and the rest rows are genotypes,
  and the file is tab delimited.

  @param     filename: a file name or file object
  @type      filename: str or file object
  @param       format: text string expected in the first header field to
                       indicate data format, if specified
  @type        format: string
  @param        limit: limit the number of columms loaded
  @type         limit: int or None
  @param       unique: flag indicating if repeated row or column elements do not exist
                       (default is True)
  @type        unique: bool
  @param       genome: genome descriptor
  @type        genome: Genome instance
  @param    chunksize: size of chunks to write/compress in bytes
  @type     chunksize: int
  @param      scratch: the buffer space available to use while reading or writing a binary file.
  @type       scratch: int
  @return:             format and sequence of column names followed by
                       tuples of row label and row data
  @rtype:              tuple of string and generator

  >>> loci =         (   'l1',       'l2',        'l3'  )
  >>> rows = [('s1', ( ('A','A'), (None,None), ('T','T'))),
  ...         ('s2', ((None,None), ('C','T'),  ('G','T'))),
  ...         ('s3', ( ('A','T'),  ('T','C'),  ('G','G')))]
  >>> genos = GenomatrixStream.from_tuples(rows,'sdat',loci=loci)
  >>> import tempfile
  >>> f = tempfile.NamedTemporaryFile()
  >>> save_genomatrix_binary(f.name,genos)
  >>> genos = load_genomatrix_binary(f.name,'sdat')
  >>> genos.format
  'sdat'
  >>> genos.columns
  ('l1', 'l2', 'l3')
  >>> for row in genos:
  ...   print row
  ('s1', [('A', 'A'), (None, None), ('T', 'T')])
  ('s2', [(None, None), ('C', 'T'), ('G', 'T')])
  ('s3', [('A', 'T'), ('C', 'T'), ('G', 'G')])

  >>> genos = load_genomatrix_binary(f.name,'ldat')
  >>> genos.format
  'ldat'
  >>> genos.columns
  ('s1', 's2', 's3')
  >>> for row in genos:
  ...   print row
  ('l1', [('A', 'A'), (None, None), ('A', 'T')])
  ('l2', [(None, None), ('C', 'T'), ('C', 'T')])
  ('l3', [('T', 'T'), ('G', 'T'), ('G', 'G')])
  '''
  if compressed_filename(filename):
    raise ValueError('Binary genotype files must not have a compressed extension')

  gfile = tables.openFile(filename,mode='r')

  format_found   = _get_v_attr(gfile,['GLU_FORMAT', 'format'])
  version        = _get_v_attr(gfile,['GLU_VERSION','version'],1)
  compat_version = _get_v_attr(gfile,['GLU_COMPAT_VERSION'],version)

  if format not in ('ldat','sdat'):
    raise ValueError, 'Unknown format: %s' % format

  if compat_version > GENOMATRIX_VERSION:
    raise ValueError('Unknown Genomatrix file version: %s' % version)

  if version > GENOMATRIX_VERSION:
    version = compat_version

  columns = tuple(imap(intern,map(str,gfile.root.cols[:])))
  rows    = tuple(imap(intern,map(str,gfile.root.rows[:])))

  if format_found == 'sdat':
    samples  = rows
    loci     = columns
  else:
    samples  = columns
    loci     = rows

  if unique:
    if len(set(columns)) != len(columns):
      raise ValueError('Non-unique column identifiers')
    if len(set(rows)) != len(rows):
      raise ValueError('Non-unique column identifiers')

  file_genome,models = load_models(gfile,loci,version)

  if format == format_found == 'sdat':
    def _load():
      descr = GenotypeArrayDescriptor(models)

      chunksize = max(2, int(scratch//gfile.root.genotypes.rowsize))
      chunks    = int(len(rows)+chunksize-1)//chunksize

      stop = 0
      for i in xrange(chunks):
        start,stop = stop,stop+chunksize
        labels = rows[start:stop]
        chunk  = gfile.root.genotypes[start:stop,:]
        for j,label in enumerate(labels):
          g = GenotypeArray(descr)
          g.data = chunk[j,:]
          yield label,g

      gfile.close()

  elif format == format_found == 'ldat':
    def _load():
      descrcache = {}

      chunksize = max(2, int(scratch//gfile.root.genotypes.rowsize))
      chunks    = int(len(rows)+chunksize-1)//chunksize

      stop = 0
      mods = iter(models)
      for i in xrange(chunks):
        start,stop = stop,stop+chunksize
        labels = rows[start:stop]
        chunk  = gfile.root.genotypes[start:stop,:]
        for j,label in enumerate(labels):
          model = mods.next()
          descr = descrcache.get(model)
          if descr is None:
            descr = descrcache[model] = GenotypeArrayDescriptor( [model]*len(samples) )
          g = GenotypeArray(descr)
          g.data = chunk[j,:]
          yield label,g

      gfile.close()

  if format == 'ldat' and format_found == 'sdat':
    def _load():
      descr = GenotypeArrayDescriptor(models)

      chunkrows,chunkcols = gfile.root.genotypes.chunkshape
      chunksize = max(1,int(scratch/(chunkcols*len(rows))))*chunkcols
      chunkbits = chunksize*8
      chunks    = int((gfile.root.genotypes.rowsize+chunksize-1)//chunksize)

      stopbit = 0
      stop    = 0
      mods = iter(models)
      for i in xrange(chunks):
        start    = stop
        startbit = stopbit

        # Note: O(N) sequential search.  This could be done via binary search
        while (stopbit-startbit) < chunkbits and stop < len(columns):
          stop   += 1
          stopbit = descr.offsets[stop]

        labels     = columns[start:stop]
        startbyte  = int(startbit//8)
        stopbyte   = int((stopbit+7)//8)
        offset     = int(startbit%8)
        chunk      = gfile.root.genotypes[:,startbyte:stopbyte]
        chunkdescr = GenotypeArrayDescriptor(models[start:stop],initial_offset=offset)

        chunkgenos = []
        for j in xrange(len(rows)):
          g = GenotypeArray(chunkdescr)
          g.data = chunk[j,:]
          chunkgenos.append(g[:])

        for j,label in enumerate(labels):
          coldescr = GenotypeArrayDescriptor( [mods.next()]*len(rows) )
          g = GenotypeArray(coldescr, imap(itemgetter(j), chunkgenos))
          yield label,g

      gfile.close()

  elif format == 'sdat' and format_found == 'ldat':
    def _load():
      descr = GenotypeArrayDescriptor(models)

      chunkrows,chunkcols = gfile.root.genotypes.chunkshape
      chunksize = max(1,int(scratch/(chunkcols*len(rows))))*chunkcols
      chunkbits = chunksize*8
      chunks    = int((gfile.root.genotypes.rowsize+chunksize-1)//chunksize)

      stopbit = 0
      stop    = 0
      for i in xrange(chunks):
        start    = stop
        startbit = stopbit

        # Note: O(N) sequential search.  This could be done via binary search
        while (stopbit-startbit) < chunkbits and stop < len(columns):
          stop   += 1
          stopbit = descr.offsets[stop]

        labels     = columns[start:stop]
        startbyte  = int(startbit//8)
        stopbyte   = int((stopbit+7)//8)
        offset     = int(startbit%8)
        chunk      = gfile.root.genotypes[:,startbyte:stopbyte]

        chunkgenos = []
        for j in xrange(len(rows)):
          chunkdescr = GenotypeArrayDescriptor([models[j]]*(stop-start),initial_offset=offset)
          g = GenotypeArray(chunkdescr)
          g.data = chunk[j,:]
          chunkgenos.append(g[:])

        for j,label in enumerate(labels):
          g = GenotypeArray(descr, imap(itemgetter(j), chunkgenos))
          yield label,g

      gfile.close()

  genos = GenomatrixStream(_load(),format,samples=samples,loci=loci,models=models,genome=file_genome,
                                         unique=unique,packed=True)

  if genome:
    genos = genos.transformed(recode_models=genome)

  return genos


def test(descr,filename,command,genotypes):
  import os
  import time

  t = time.time()
  command(filename)
  t = time.time()-t
  s = os.stat(filename).st_size
  gps,bpg = genotypes/t,8.0*s/genotypes
  print '%-38s  %6.2fs  %10d  %10d  %6.2f'  % (descr,t,s,gps,bpg)


def main():
  from   random    import shuffle

  from   glu.lib.utils         import ilen

  from   glu.lib.genolib.reprs import snp
  from   glu.lib.genolib.io    import load_genostream, save_genostream

  if 0:
    f      = '/usr/local/share/hapmap/build21/fwd_strand/non-redundant/genotypes_chr22_CEU_r21a_nr_fwd.txt.gz'
    #f     = '/usr/local/share/hapmap/build21/fwd_strand/non-redundant/genotypes_chr2_YRI_r21a_nr_fwd.txt.gz'
    matrix = load_genostream(f,format='hapmap').materialize()
    format = 'hapmap'
  else:
    f = '/home/jacobske/projects/CGEMS/Scans/Breast/1/current/genotypes/STUDY/subjects_STUDY_CASE_22.ldat.gz'
    matrix = load_genostream(f).materialize()
    format = matrix.format

  matrix   = matrix.materialize()

  g = len(matrix.loci)*len(matrix.samples)
  print 'DATA: %d loci, %d subjects, %d genotypes' % (len(matrix.loci),len(matrix.samples),g)
  print

  test('Load   compressed %s file' % (format or 'source'), f,
         lambda f: ilen(load_genostream(f,format=format)), g)

  if 1:
    test('Save uncompressed   triple file ldat', 'data/g2.trip',
           lambda f: save_genostream(f,matrix.as_genotriples()), g)
    test('Save   compressed   triple file ldat', 'data/g2.trip.gz',
           lambda f: save_genostream(f,matrix.as_genotriples()), g)
    test('Save       binary   triple file ldat', 'data/g2.tbat',
           lambda f: save_genostream(f,matrix.as_genotriples()), g)
    test('Load uncompressed   triple file ldat', 'data/g2.trip',
           lambda f: ilen(load_genostream(f)), g)
    test('Load   compressed   triple file ldat', 'data/g2.trip.gz',
           lambda f: ilen(load_genostream(f)), g)
    test('Load       binary   triple file ldat', 'data/g2.tbat',
           lambda f: ilen(load_genostream(f)), g)

  if 1:
    test('Save   compressed   ldat file',        'data/g2.ldat.gz',
           lambda f: save_genostream(f,matrix), g)

  if 1:
    test('Save uncompressed   ldat file',        'data/g2.ldat',
           lambda f: save_genostream(f,matrix), g)
    test('Load   compressed   ldat file',        'data/g2.ldat.gz',
           lambda f: ilen(load_genostream(f)), g)
    test('Load uncompressed   ldat file',        'data/g2.ldat',
           lambda f: ilen(load_genostream(f)), g)

  if 1:
    test('Save       binary   ldat file',        'data/g2.lbat',
           lambda f: save_genostream(f,matrix), g)

  if 1:
    test('Load       binary   ldat file',        'data/g2.lbat',
           lambda f: ilen(load_genostream(f)), g)

  test('Save      ubinary   ldat file',        'data/u2.lbat',
         lambda f: save_genostream(f,matrix,compress=False), g)
  test('Load      ubinary   ldat file',        'data/u2.lbat',
         lambda f: ilen(load_genostream(f)), g)

  test('Load       binary   ldat file as sdat', 'data/g2.lbat',
         lambda f: ilen(load_genomatrix_binary(f,'sdat')), g)

  test('Load   compressed   ldat file as sdat',        'data/g2.ldat.gz',
         lambda f: ilen(load_genostream(f).as_sdat()), g)

  matrix  = None

  # Materialize for use later (but don't time)
  if 1:
    matrix2 = load_genomatrix_binary('data/g2.lbat','sdat').materialize()

  test('Save       binary   sdat file',        'data/g22.sbat',
         lambda f: save_genostream(f,matrix2), g)
  test('Load       binary   sdat file',        'data/g22.sbat',
         lambda f: ilen(load_genostream(f)), g)
  test('Save      ubinary   sdat file',        'data/u22.sbat',
         lambda f: save_genostream(f,matrix2,compress=False), g)
  test('Load      ubinary   sdat file',        'data/u22.sbat',
         lambda f: ilen(load_genostream(f)), g)

  test('Load       binary   sdat file as ldat', 'data/g22.sbat',
         lambda f: ilen(load_genomatrix_binary(f,'ldat')), g)

  test('Save   compressed   sdat file',        'data/g2.sdat.gz',
         lambda f: save_genostream(f,matrix2), g)
  test('Save uncompressed   sdat file',        'data/g2.sdat',
         lambda f: save_genostream(f,matrix2), g)
  test('Load   compressed   sdat file',        'data/g2.sdat.gz',
         lambda f: ilen(load_genostream(f)), g)
  test('Load uncompressed   sdat file',        'data/g2.sdat',
         lambda f: ilen(load_genostream(f)), g)

  test('Load   compressed   sdat file as ldat','data/g2.sdat.gz',
         lambda f: ilen(load_genostream(f).as_ldat()), g)

  if 1:
    test('Save uncompressed   triple file sdat', 'data/g22.trip',
           lambda f: save_genostream(f,matrix2.as_genotriples()), g)
    test('Save   compressed   triple file sdat', 'data/g22.trip.gz',
           lambda f: save_genostream(f,matrix2.as_genotriples()), g)
    test('Save       binary   triple file sdat', 'data/g23.tbat',
           lambda f: save_genostream(f,matrix2.as_genotriples()), g)
    test('Load uncompressed   triple file sdat', 'data/g22.trip',
           lambda f: ilen(load_genostream(f)), g)
    test('Load   compressed   triple file sdat', 'data/g22.trip.gz',
           lambda f: ilen(load_genostream(f)), g)
    test('Load       binary   triple file sdat', 'data/g23.tbat',
           lambda f: ilen(load_genostream(f)), g)

  if 0: # VERY VERY VERY VERY (VERY!) EXPENSIVE
    triples.clone(shuffle(list(matrix2.as_genotriples())))

    test('Save uncompressed   triple file random', 'data/g32.trip',
           lambda f: save_genostream(f,triples), g)
    test('Save   compressed   triple file random', 'data/g32.trip.gz',
           lambda f: save_genostream(f,triples), g)
    test('Save       binary   triple file random', 'data/g33.tbat',
           lambda f: save_genostream_binary(f,triples), g)
    test('Load uncompressed   triple file random', 'data/g32.trip',
           lambda f: ilen(load_genostream(f)), g)
    test('Load   compressed   triple file random', 'data/g32.trip.gz',
           lambda f: ilen(load_genostream(f)), g)
    test('Load       binary   triple file random', 'data/g33.tbat',
           lambda f: ilen(load_genostream(f)), g)


def _test():
  import doctest
  return doctest.testmod()


if __name__ == '__main__':
  _test()
  if 0:
    pass
  elif 1:
    main()
  else:
    try:
      import cProfile as profile
    except ImportError:
      import profile
    import pstats

    prof = profile.Profile()
    try:
      prof.runcall(main)
    finally:
      stats = pstats.Stats(prof)
      stats.strip_dirs()
      stats.sort_stats('time', 'calls')
      stats.print_stats(25)
