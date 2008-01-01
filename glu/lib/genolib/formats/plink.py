# -*- coding: utf-8 -*-
'''
File:          plink.py

Authors:       Kevin Jacobs (jacobske@bioinformed.com)

Created:       2006-01-01

Abstract:      GLU PLINK genotype format input/output objects

Requires:      Python 2.5

Revision:      $Id$
'''

from __future__ import with_statement

__copyright__ = 'Copyright (c) 2007 Science Applications International Corporation ("SAIC")'
__license__   = 'See GLU license for terms by running: glu license'


from   itertools                 import islice,izip

from   glu.lib.utils             import gcdisabled
from   glu.lib.fileutils         import autofile,namefile,               \
                                        guess_related_file,related_file, \
                                        parse_augmented_filename,get_arg

from   glu.lib.genolib.streams   import GenomatrixStream
from   glu.lib.genolib.genoarray import model_from_alleles
from   glu.lib.genolib.locus     import Genome
from   glu.lib.genolib.phenos    import Phenome,SEX_MALE,SEX_FEMALE,SEX_UNKNOWN, \
                                        PHENO_UNKNOWN,PHENO_UNAFFECTED,PHENO_AFFECTED


__all__ = ['PlinkPedWriter',  'save_plink_ped',  'load_plink_ped',
           'PlinkTPedWriter', 'save_plink_tped', 'load_plink_tped',
           'load_plink_bed']


ALLELE_MAP  = {'0':None}
ALLELE_RMAP = {None:'0'}

SEX_MAP    = {'1':SEX_MALE,'2':SEX_FEMALE}
SEX_RMAP   = {SEX_UNKNOWN:'0', SEX_MALE:'1', SEX_FEMALE:'2'}

PHENO_MAP  = {'1':PHENO_UNAFFECTED, '2':PHENO_AFFECTED}
PHENO_RMAP = {PHENO_UNKNOWN:'0',PHENO_UNAFFECTED:'1',PHENO_AFFECTED:'2'}

PARENT_MAP = {'0':None}


def load_plink_map(filename,genome):
  mfile = autofile(filename)

  for i,line in enumerate(mfile):
    line = line.rstrip()

    if not line or line.startswith('#'):
      continue

    fields = line.split()

    if len(fields) != 4:
      raise ValueError('Invalid PLINK MAP record %d' % (i+1))

    chr   = fields[0]
    lname = fields[1]
    gdist = int(fields[2])      if fields[2] else None
    pdist = abs(int(fields[3])) if fields[3] else None

    if not lname:
      raise ValueError('Invalid PLINK MAP record %d' % (i+1))

    if chr == '0':
      chr = None

    genome.merge_locus(lname, chromosome=chr, location=pdist)

    yield lname


def load_plink_ped(filename,genome=None,phenome=None,extra_args=None,**kwargs):
  '''
  Load a PLINK PED format genotype data file.

  @param     filename: file name or file object
  @type      filename: str or file object
  @param       genome: genome descriptor
  @type        genome: Genome instance
  @param      phenome: phenome descriptor
  @type       phenome: Phenome instance
  @param       unique: rows and columns are uniquely labeled (default is True)
  @type        unique: bool
  @param   extra_args: optional dictionary to store extraneous arguments, instead of
                       raising an error.
  @type    extra_args: dict
  @rtype             : GenomatrixStream
  '''
  if extra_args is None:
    args = kwargs
  else:
    args = extra_args
    args.update(kwargs)

  filename = parse_augmented_filename(filename,args)

  unique = get_arg(args, ['unique'], True)
  loci   = get_arg(args, ['loci'])
  lmap   = get_arg(args, ['map']) or guess_related_file(filename,['map'])

  if loci is None and lmap is None:
    raise ValueError('Map file or locus list must be specified when loading PLINK PED files')

  if extra_args is None and args:
    raise ValueError('Unexpected filename arguments: %s' % ','.join(sorted(args)))

  if genome is None:
    genome = Genome()

  if phenome is None:
    phenome = Phenome()

  if loci and isinstance(loci,basestring):
    loci = list(load_locus_records(loci)[2])
    # Merge map data into genome
    populate_genome(genome,loci)
    loci = [ intern(l[0]) for l in loci ]

  if dat:
    map_loci = list(load_merlin_dat(dat,genome))
    if not loci:
      loci = map_loci
    elif loci != map_loci:
      raise ValueError('Locus list and PLINK MAP file are not identical')

  loci = loci or []

  gfile = autofile(filename)
  n     = 6 + 2*len(loci)

  def _load_plink():
    for line_num,line in enumerate(gfile):
      if not line or line.startswith('#'):
        continue

      with gcdisabled:
        fields = line.split()

        if len(fields) != n:
          raise ValueError('Invalid record on line %d of %s' % (line_num+1,namefile(filename)))

        family,name,father,mother,sex,pheno = [ s.strip() for s in fields[:6] ]

        if name == '0':
          raise ValueError('Invalid record on line %d of %s' % (line_num+1,namefile(filename)))

        father  = PARENT_MAP.get(father,father)
        mother  = PARENT_MAP.get(mother,mother)

        ename   = '%s:%s' % (family,name)
        efather = '%s:%s' % (family,father) if father else None
        emother = '%s:%s' % (family,mother) if mother else None
        sex     = SEX_MAP.get(sex,SEX_UNKNOWN)
        pheno   = PHENO_MAP.get(pheno,PHENO_UNKNOWN)

        if father:
          phenome.merge_phenos(efather, family, father, sex=SEX_MALE)
        if mother:
          phenome.merge_phenos(emother, family, mother, sex=SEX_FEMALE)

        phenome.merge_phenos(ename, family, name, efather, emother, sex, pheno)

        fields = [ ALLELE_MAP.get(a,a) for a in islice(fields,6,None) ]
        genos  = zip(islice(fields,0,None,2),islice(fields,1,None,2))

      yield ename,genos

  genos = GenomatrixStream.from_tuples(_load_plink(),'sdat',loci=loci,genome=genome,phenome=phenome,unique=unique)

  if unique:
    genos = genos.unique_checked()

  return genos


class PlinkPedWriter(object):
  '''
  Object to write a matrix data to a PLINK PED file

  >>> loci =           ('l1',      'l2',      'l3')
  >>> rows = [('s1', [('A','A'),(None,None),('C','T')]),
  ...         ('s2', [('A','G'), ('C','G'), ('C','C')]),
  ...         ('s3', [('G','G'),(None,None),('C','T')]) ]
  >>> genos = GenomatrixStream.from_tuples(rows,'sdat',loci=loci)
  >>> from cStringIO import StringIO
  >>> o = StringIO()
  >>> m = StringIO()
  >>> with PlinkPedWriter(o,genos.loci,genos.genome,genos.phenome,mapfile=m) as w:
  ...   genos=iter(genos)
  ...   w.writerow(*genos.next())
  ...   w.writerow(*genos.next())
  ...   w.writerows(genos)
  >>> print o.getvalue() # doctest: +NORMALIZE_WHITESPACE
  s1 s1 0 0 0 0 A A 0 0 C T
  s2 s2 0 0 0 0 A G C G C C
  s3 s3 0 0 0 0 G G 0 0 C T
  >>> print m.getvalue() # doctest: +NORMALIZE_WHITESPACE
  0 l1 0 0
  0 l2 0 0
  0 l3 0 0
  '''
  def __init__(self,filename,loci,genome,phenome,extra_args=None,**kwargs):
    '''
    @param     filename: file name or file object
    @type      filename: str or file object
    @param       format: data format string
    @type        format: str
    @param         loci: locus names
    @type          loci: list of str
    @param       genome: genome descriptor
    @type        genome: Genome instance
    @param      phenome: phenome descriptor
    @type       phenome: Phenome instance
    '''
    if extra_args is None:
      args = kwargs
    else:
      args = extra_args
      args.update(kwargs)

    filename = parse_augmented_filename(filename,args)

    mapfile  = get_arg(args, ['mapfile','map'])

    # Careful: mapfile=<blank> is intended to supress output
    if mapfile is None:
      mapfile = related_file(filename,'map')

    if extra_args is None and args:
      raise ValueError('Unexpected filename arguments: %s' % ','.join(sorted(args)))

    self.out       = autofile(filename,'wb')
    self.loci      = loci
    self.genome    = genome
    self.phenome   = phenome
    self.mapfile   = mapfile

  def writerow(self, sample, genos, phenome=None):
    '''
    Write a row of genotypes given the row key and list of genotypes

    @param rowkey: row identifier
    @type  rowkey: str
    @param  genos: sequence of genotypes in an internal representation
    @type   genos: sequence
    '''
    out = self.out
    if out is None:
      raise IOError('Cannot write to closed writer object')

    if len(genos) != len(self.loci):
      raise ValueError('[ERROR] Internal error: Genotypes do not match header')

    if phenome is None:
      phenome = self.phenome
    if phenome is None:
      phenome = Phenome()

    phenos     = phenome.get_phenos(sample)
    family     = phenos.family
    individual = phenos.individual or sample
    parent1    = phenos.parent1
    parent2    = phenos.parent2

    if parent1 and parent2:
      p1 = phenome.get_phenos(parent1)
      p2 = phenome.get_phenos(parent2)
      if p1.sex is SEX_FEMALE or p2.sex is SEX_MALE:
        parent1,parent2 = parent2,parent1

    sex   = SEX_RMAP[phenos.sex]
    pheno = PHENO_RMAP[phenos.phenoclass]

    row = [family or individual,individual,parent1 or '0',parent2 or '0',sex,pheno]
    for g in genos:
      row += [ ALLELE_RMAP.get(a,a) for a in g ]
    out.write(' '.join(row))
    out.write('\r\n')

  def writerows(self, rows, phenome=None):
    '''
    Write rows of genotypes given pairs of row key and list of genotypes

    @param rows: sequence of pairs of row key and sequence of genotypes in
                 an internal representation
    @type  rows: sequence of (str,sequence)
    '''
    out = self.out
    if out is None:
      raise IOError('Cannot write to closed writer object')

    n = len(self.loci)

    if phenome is None:
      phenome = self.phenome
    if phenome is None:
      phenome = Phenome()

    for sample,genos in rows:
      if len(genos) != n:
        raise ValueError('[ERROR] Internal error: Genotypes do not match header')

      phenos     = phenome.get_phenos(sample)
      family     = phenos.family
      individual = phenos.individual or sample
      parent1    = phenos.parent1
      parent2    = phenos.parent2

      if parent1 and parent2:
        p1 = phenome.get_phenos(parent1)
        p2 = phenome.get_phenos(parent2)
        if p1.sex is SEX_FEMALE or p2.sex is SEX_MALE:
          parent1,parent2 = parent2,parent1

      sex   = SEX_RMAP[phenos.sex]
      pheno = PHENO_RMAP[phenos.phenoclass]

      row = [family or individual,individual,parent1 or '0',parent2 or '0',sex,pheno]

      for g in genos:
        row += [ ALLELE_RMAP.get(a,a) for a in g ]

      out.write(' '.join(row))
      out.write('\r\n')

  def close(self):
    '''
    Close the writer

    A closed writer cannot be used for further I/O operations and will
    result in an error if called more than once.
    '''
    if self.out is None:
      raise IOError('Writer object already closed')

    # FIXME: Closing out causes problems with StringIO objects used for
    #        testing
    #self.out.close()
    self.out = None

    if self.mapfile:
      out = autofile(self.mapfile,'wb')
      for locus in self.loci:
        loc = self.genome.get_locus(locus)
        out.write( ' '.join( map(str,[loc.chromosome or 0,locus,0,loc.location or 0] )) )
        out.write('\r\n')

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


def save_plink_ped(filename,genos,extra_args=None,**kwargs):
  '''
  Write the genotype matrix data to file.

  @param     filename: file name or file object
  @type      filename: str or file object
  @param        genos: genomatrix stream
  @type         genos: sequence

  >>> from cStringIO import StringIO
  >>> o = StringIO()
  >>> loci =              ('l1',     'l2',    'l3')
  >>> rows = [('s1', [('A','A'),(None,None),('C','T')]),
  ...           ('s2', [('A','G'), ('C','G'), ('C','C')]),
  ...           ('s3', [('G','G'),(None,None),('C','T')]) ]
  >>> genos = GenomatrixStream.from_tuples(rows,'sdat',loci=loci)
  >>> save_plink_ped(o,genos)
  >>> print o.getvalue() # doctest: +NORMALIZE_WHITESPACE
  s1 s1 0 0 0 0 A A 0 0 C T
  s2 s2 0 0 0 0 A G C G C C
  s3 s3 0 0 0 0 G G 0 0 C T
  '''
  if extra_args is None:
    args = kwargs
  else:
    args = extra_args
    args.update(kwargs)

  filename  = parse_augmented_filename(filename,args)

  mergefunc = get_arg(args, ['mergefunc'])

  genos = genos.as_sdat(mergefunc)

  with PlinkPedWriter(filename, genos.loci, genos.genome, genos.phenome,
                                extra_args=args) as writer:

    if extra_args is None and args:
      raise ValueError('Unexpected filename arguments: %s' % ','.join(sorted(args)))

    writer.writerows(genos)


###############################################################################################


def load_plink_tfam(filename,phenome):
  mfile = autofile(filename)

  for i,line in enumerate(mfile):
    line = line.rstrip()

    if not line or line.startswith('#'):
      continue

    fields = line.split()

    if len(fields) != 6:
      raise ValueError('Invalid PLINK TFAM record %d' % (i+1))

    family,name,father,mother,sex,pheno = [ s.strip() for s in fields ]

    if name == '0':
      raise ValueError('Invalid record on line %d of %s' % (line_num+1,namefile(filename)))

    father  = PARENT_MAP.get(father,father)
    mother  = PARENT_MAP.get(mother,mother)

    ename   = '%s:%s' % (family,name)
    efather = '%s:%s' % (family,father) if father else None
    emother = '%s:%s' % (family,mother) if mother else None
    sex     = SEX_MAP.get(sex,SEX_UNKNOWN)
    pheno   = PHENO_MAP.get(pheno,PHENO_UNKNOWN)

    if father:
      phenome.merge_phenos(efather, family, father, sex=SEX_MALE)
    if mother:
      phenome.merge_phenos(emother, family, mother, sex=SEX_FEMALE)

    phenome.merge_phenos(ename, family, name, efather, emother, sex, pheno)

    yield ename


def load_plink_tped(filename,genome=None,phenome=None,extra_args=None,**kwargs):
  '''
  Load a PLINK TPED format genotype data file.

  @param     filename: file name or file object
  @type      filename: str or file object
  @param       genome: genome descriptor
  @type        genome: Genome instance
  @param      phenome: phenome descriptor
  @type       phenome: Phenome instance
  @param       unique: rows and columns are uniquely labeled (default is True)
  @type        unique: bool
  @param   extra_args: optional dictionary to store extraneous arguments, instead of
                       raising an error.
  @type    extra_args: dict
  @rtype             : GenomatrixStream
  '''
  if extra_args is None:
    args = kwargs
  else:
    args = extra_args
    args.update(kwargs)

  filename = parse_augmented_filename(filename,args)

  unique = get_arg(args, ['unique'], True)
  loci   = get_arg(args, ['loci'])
  tfam   = get_arg(args, ['tfam','fam']) or guess_related_file(filename,['tfam','fam'])

  if tfam is None:
    raise ValueError('A TFAM file must be specified when loading PLINK TPED data')

  if extra_args is None and args:
    raise ValueError('Unexpected filename arguments: %s' % ','.join(sorted(args)))

  if genome is None:
    genome = Genome()

  if phenome is None:
    phenome = Phenome()

  if loci and isinstance(loci,basestring):
    loci = list(load_locus_records(loci)[2])
    # Merge map data into genome
    populate_genome(genome,loci)
    loci = [ intern(l[0]) for l in loci ]

  loci = loci or []

  samples = list(load_plink_tfam(tfam,phenome))

  gfile = autofile(filename)
  n     = 4 + 2*len(samples)

  def _load_plink():
    for line_num,line in enumerate(gfile):
      if not line or line.startswith('#'):
        continue

      with gcdisabled:
        fields = line.split()

        if len(fields) != n:
          raise ValueError('Invalid record on line %d of %s' % (line_num+1,namefile(filename)))

        chr   = fields[0] or None
        lname = fields[1]
        gdist = int(fields[2])      if fields[2] else None
        pdist = abs(int(fields[3])) if fields[3] else None

        if not lname:
          raise ValueError('Invalid PLINK TPED record %d' % (i+1))

        if chr == '0':
          chr = None

        genome.merge_locus(lname, chromosome=chr, location=pdist)

        fields = [ ALLELE_MAP.get(a,a) for a in islice(fields,4,None) ]
        genos  = zip(islice(fields,0,None,2),islice(fields,1,None,2))

      yield lname,genos

  genos = GenomatrixStream.from_tuples(_load_plink(),'ldat',samples=samples,genome=genome,phenome=phenome,unique=unique)

  if unique:
    genos = genos.unique_checked()

  return genos


class PlinkTPedWriter(object):
  '''
  Object to write a PLINK TPED file

  See http://pngu.mgh.harvard.edu/~purcell/plink/data.shtml#tr

  >>> loci =           ('l1',      'l2',      'l3')
  >>> rows = [('s1', [('A','A'),(None,None),('C','T')]),
  ...         ('s2', [('A','G'), ('C','G'), ('C','C')]),
  ...         ('s3', [('G','G'),(None,None),('C','T')]) ]
  >>> genos = GenomatrixStream.from_tuples(rows,'sdat',loci=loci).as_ldat()
  >>> from cStringIO import StringIO
  >>> o = StringIO()
  >>> m = StringIO()
  >>> with PlinkTPedWriter(o,genos.samples,genos.genome,genos.phenome,tfamfile=m) as w:
  ...   genos=iter(genos)
  ...   w.writerow(*genos.next())
  ...   w.writerow(*genos.next())
  ...   w.writerows(genos)
  >>> print o.getvalue() # doctest: +NORMALIZE_WHITESPACE
  0 l1 0 0 A A A G G G
  0 l2 0 0 0 0 C G 0 0
  0 l3 0 0 C T C C C T
  >>> print m.getvalue() # doctest: +NORMALIZE_WHITESPACE
  s1 s1 0 0 0 0
  s2 s2 0 0 0 0
  s3 s3 0 0 0 0
  '''
  def __init__(self,filename,samples,genome,phenome,extra_args=None,**kwargs):
    '''
    @param     filename: file name or file object
    @type      filename: str or file object
    @param       format: data format string
    @type        format: str
    @param      samples: sample names
    @type       samples: list of str
    @param       genome: genome descriptor
    @type        genome: Genome instance
    @param      phenome: phenome descriptor
    @type       phenome: Phenome instance
    '''
    if extra_args is None:
      args = kwargs
    else:
      args = extra_args
      args.update(kwargs)

    filename = parse_augmented_filename(filename,args)

    tfamfile = get_arg(args, ['tfamfile','tfam'])

    # Careful: mapfile=<blank> is intended to supress output
    if tfamfile is None:
      tfamfile = related_file(filename,'tfam')

    if extra_args is None and args:
      raise ValueError('Unexpected filename arguments: %s' % ','.join(sorted(args)))

    self.out       = autofile(filename,'wb')
    self.samples   = samples
    self.genome    = genome
    self.phenome   = phenome
    self.tfamfile  = tfamfile

  def writerow(self, locus, genos):
    '''
    Write a row of genotypes given the row key and list of genotypes

    @param rowkey: row identifier
    @type  rowkey: str
    @param  genos: sequence of genotypes in an internal representation
    @type   genos: sequence
    '''
    out = self.out
    if out is None:
      raise IOError('Cannot write to closed writer object')

    if len(genos) != len(self.samples):
      raise ValueError('[ERROR] Internal error: Genotypes do not match header')

    loc = self.genome.get_locus(locus)

    row = map(str,[loc.chromosome or 0,locus,0,loc.location or 0] )

    for g in genos:
      row += [ ALLELE_RMAP.get(a,a) for a in g ]

    out.write(' '.join(row))
    out.write('\r\n')

  def writerows(self, rows):
    '''
    Write rows of genotypes given pairs of row key and list of genotypes

    @param rows: sequence of pairs of row key and sequence of genotypes in
                 an internal representation
    @type  rows: sequence of (str,sequence)
    '''
    out = self.out
    if out is None:
      raise IOError('Cannot write to closed writer object')

    n = len(self.samples)

    for locus,genos in rows:
      if len(genos) != n:
        raise ValueError('[ERROR] Internal error: Genotypes do not match header')

      loc = self.genome.get_locus(locus)

      row = map(str,[loc.chromosome or 0,locus,0,loc.location or 0] )

      for g in genos:
        row += [ ALLELE_RMAP.get(a,a) for a in g ]

      out.write(' '.join(row))
      out.write('\r\n')

  def close(self):
    '''
    Close the writer

    A closed writer cannot be used for further I/O operations and will
    result in an error if called more than once.
    '''
    if self.out is None:
      raise IOError('Writer object already closed')

    # FIXME: Closing out causes problems with StringIO objects used for
    #        testing
    #self.out.close()
    self.out = None

    if self.tfamfile:
      out = autofile(self.tfamfile,'wb')
      for sample in self.samples:
        phenos     = self.phenome.get_phenos(sample)
        family     = phenos.family
        individual = phenos.individual or sample
        parent1    = phenos.parent1
        parent2    = phenos.parent2

        if parent1 and parent2:
          p1 = phenome.get_phenos(parent1)
          p2 = phenome.get_phenos(parent2)
          if p1.sex is SEX_FEMALE or p2.sex is SEX_MALE:
            parent1,parent2 = parent2,parent1

        sex   = SEX_RMAP[phenos.sex]
        pheno = PHENO_RMAP[phenos.phenoclass]

        row = [family or individual,individual,parent1 or '0',parent2 or '0',sex,pheno]
        out.write( ' '.join(row))
        out.write('\r\n')

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


def save_plink_tped(filename,genos,extra_args=None,**kwargs):
  '''
  Write the genotype matrix data to file.

  @param     filename: file name or file object
  @type      filename: str or file object
  @param        genos: genomatrix stream
  @type         genos: sequence

  >>> from cStringIO import StringIO
  >>> o = StringIO()
  >>> loci =              ('l1',     'l2',    'l3')
  >>> rows = [('s1', [('A','A'),(None,None),('C','T')]),
  ...           ('s2', [('A','G'), ('C','G'), ('C','C')]),
  ...           ('s3', [('G','G'),(None,None),('C','T')]) ]
  >>> genos = GenomatrixStream.from_tuples(rows,'sdat',loci=loci)
  >>> save_plink_tped(o,genos)
  >>> print o.getvalue() # doctest: +NORMALIZE_WHITESPACE
  0 l1 0 0 A A A G G G
  0 l2 0 0 0 0 C G 0 0
  0 l3 0 0 C T C C C T
  '''
  if extra_args is None:
    args = kwargs
  else:
    args = extra_args
    args.update(kwargs)

  filename  = parse_augmented_filename(filename,args)

  mergefunc = get_arg(args, ['mergefunc'])

  genos = genos.as_ldat(mergefunc)

  with PlinkTPedWriter(filename, genos.samples, genos.genome, genos.phenome,
                                 extra_args=args) as writer:

    if extra_args is None and args:
      raise ValueError('Unexpected filename arguments: %s' % ','.join(sorted(args)))

    writer.writerows(genos)


##############################################################################################


def load_plink_bim(filename,genome):
  mfile = autofile(filename)

  modelcache = {}

  for i,line in enumerate(mfile):
    line = line.rstrip()

    if not line or line.startswith('#'):
      continue

    fields = line.split()

    if len(fields) != 6:
      raise ValueError('Invalid PLINK BIM record %d' % (i+1))

    chr     = fields[0]
    locus   = fields[1]
    gdist   = int(fields[2])      if fields[2] else None
    pdist   = abs(int(fields[3])) if fields[3] else None
    allele1 = fields[4]
    allele2 = fields[5]

    if not locus:
      raise ValueError('Invalid PLINK BIM record %d' % (i+1))

    if chr == '0':
      chr = None

    alleles = []

    if allele1 == '0':
      allele1 = None
    else:
      alleles.append(allele1)

    if allele2 == '0':
      allele2 = None
    else:
      alleles.append(allele2)

    alleles = tuple(sorted(alleles))

    model = genome.get_locus(locus).model

    if not model:
      model = modelcache.get(alleles)

    if not model:
      model = modelcache[alleles] = model_from_alleles(alleles,max_alleles=2)

    genome.merge_locus(locus, model, True, chr, pdist)

    yield locus,allele1,allele2


def _plink_decode(model,allele1,allele2):
  genos = [model.genotypes[0]]*4

  if allele1:
    genos[0] = model.add_genotype( (allele1,allele1) )
  if allele2:
    genos[3] = model.add_genotype( (allele2,allele2) )
  if allele1 and allele2:
    genos[2] = model.add_genotype( (allele1,allele2) )

  return genos


def load_plink_bed(filename,genome=None,phenome=None,extra_args=None,**kwargs):
  '''
  Load a PLINK BED format genotype data file.

  See http://pngu.mgh.harvard.edu/~purcell/plink/binary.shtml

  Use undocumented "--make-bed --ind-major" PLINK options to get sdat flavor.

  @param     filename: file name or file object
  @type      filename: str or file object
  @param       genome: genome descriptor
  @type        genome: Genome instance
  @param      phenome: phenome descriptor
  @type       phenome: Phenome instance
  @param   extra_args: optional dictionary to store extraneous arguments, instead of
                       raising an error.
  @type    extra_args: dict
  @rtype             : GenomatrixStream
  '''
  if extra_args is None:
    args = kwargs
  else:
    args = extra_args
    args.update(kwargs)

  filename = parse_augmented_filename(filename,args)

  gfile = autofile(filename)

  magic = map(ord,gfile.read(2))
  mode  = ord(gfile.read(1))

  if magic != [0x6c,0x1b]:
    raise ValueError('Invalid PLINK BED file magic number')

  if mode not in [0,1]:
    raise ValueError('Invalid PLINK BED file mode')

  unique = get_arg(args, ['unique'], True)
  loc    = get_arg(args, ['loci'])
  bim    = get_arg(args, ['map','bim' ]) or guess_related_file(filename,['bim'])
  fam    = get_arg(args, ['fam','tfam']) or guess_related_file(filename,['fam','tfam'])

  if bim is None:
    raise ValueError('BIM file must be specified when loading PLINK BED data')

  if fam is None:
    raise ValueError('A FAM file must be specified when loading PLINK BED data')

  if extra_args is None and args:
    raise ValueError('Unexpected filename arguments: %s' % ','.join(sorted(args)))

  if genome is None:
    genome = Genome()

  if phenome is None:
    phenome = Phenome()

  bim_loci = list(load_plink_bim(bim,genome))
  loci     = [ l[0] for l in bim_loci ]
  samples  = list(load_plink_tfam(fam,phenome))
  models   = [ genome.get_model(locus) for locus in loci ]

  if loc and isinstance(loc,basestring):
    loc = list(load_locus_records(loc)[2])
    # Merge map data into genome
    populate_genome(genome,loc)

  unique = len(set(samples))==len(samples) and len(set(loci))==len(loci)

  if mode == 0:
    format = 'sdat'

    def _load_plink():
      valuecache = {}
      genovalues = []

      for i,(locus,allele1,allele2) in enumerate(bim_loci):
        model = models[i]
        values = valuecache.get(model)

        if values is None:
          values = valuecache[model] = _plink_decode(model,allele1,allele2)

        byte  = i//4
        shift = 2*(i%4)

        genovalues.append( (byte,shift,values) )

      rowbytes = (len(loci)*2+7)//8

      for sample in samples:
        data  = map(ord,gfile.read(rowbytes))
        genos = [ values[(data[byte]>>shift)&3] for byte,shift,values in genovalues ]
        yield sample,genos

  elif mode == 1:
    format = 'ldat'

    def _load_plink():
      genovalues = []

      for i,sample in enumerate(samples):
        byte  = i//4
        shift = 2*(i%4)
        genovalues.append( (byte,shift) )

      valuecache = {}
      rowbytes = (len(samples)*2+7)//8

      for (locus,allele1,allele2),model in izip(bim_loci,models):
        data = map(ord,gfile.read(rowbytes))

        values = valuecache.get(model)

        if values is None:
          values = valuecache[model] = _plink_decode(model,allele1,allele2)

        genos = [ values[(data[byte]>>shift)&3] for byte,shift in genovalues ]

        yield locus,genos

  return GenomatrixStream(_load_plink(),format,loci=loci,samples=samples,models=models,
                                        genome=genome,phenome=phenome,unique=unique)


###############################################################################################


def test():
  import doctest
  return doctest.testmod()


if __name__ == '__main__':
  test()