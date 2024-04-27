# emojis

* Run `brew install gimp`
* Run the following (replacing with pathname where appropriate):
```sh
gimp -idf --batch-interpreter python-fu-eval \
    -b "import sys;sys.path=['.']+sys.path;import run_gimp;run_gimp.run('/Users/otan/Downloads/nathan.png')" \
    -b "pdb.gimp_quit(1)"
```
