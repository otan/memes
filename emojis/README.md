# emojis

* Run `brew install gimp`
* Get a transparent background, 64x64 (or 128x128) size of the target.
  * images should be an unindexed PNG. if they aren't, my quick trick is to convert to webp and back
* Run the following (replacing with pathname where appropriate):
```sh
gimp -idf --batch-interpreter python-fu-eval \
    -b "import sys;sys.path=['.']+sys.path;import run_gimp;run_gimp.run('/Users/otan/Downloads/nathan.png')" \
    -b "pdb.gimp_quit(1)"
```
* The emojis should now be in the same directory

This script was originally from William Ho.
