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

  from   glu.lib.genolib.formats import binary,text,hapmap,prettybase,plink,merlin,structure
  import text as local_text

  # Standardize on the .py source, since one can load the .py and the other the .pyc
  packagefile =  text.__file__.replace('.pyc','.py')
  localfile   =  local_text.__file__.replace('.pyc','.py')

  try:
    if not os.path.samefile(packagefile,localfile):
      raise OSError
  except OSError:
    raise ImportError('Your PYTHONPATH/pkg_resources are not set correctly to find this GLU tree')

  for module in text,binary,hapmap,prettybase,plink,merlin,structure:
    doctest.testmod(module,report=False)
  doctest.master.summarize()


if __name__ == '__main__':
  test()
