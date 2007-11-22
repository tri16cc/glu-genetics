# -*- coding: utf-8 -*-
'''
File:          test.py

Authors:       Kevin Jacobs (jacobske@bioinformed.com)

Created:

Abstract:      Run genolib doctest suite

Requires:      Python 2.5, glu

Revision:      $Id$
'''

import pkg_resources
pkg_resources.require('glu')


def test(report=True):
  import os
  import doctest

  from   glu.lib.genolib import bitarray,genoarray,io,merge,reprs,streams,locus
  import streams as local_streams

  # Standardize on the .py source, since one can load the .py and the other the .pyc
  packagefile =  streams.__file__.replace('.pyc','.py')
  localfile   =  local_streams.__file__.replace('.pyc','.py')

  try:
    if not os.path.samefile(packagefile,localfile):
      raise OSError
  except OSError:
    raise ImportError('Your PYTHONPATH/pkg_resources are not set correctly to find this GLU tree')

  for module in bitarray,genoarray,io,merge,reprs,streams,locus:
    doctest.testmod(module,report=False)

  from glu.lib.genolib.formats.test import test as test_formats

  test_formats(False)

  if report:
    doctest.master.summarize()


if __name__ == '__main__':
  test()
