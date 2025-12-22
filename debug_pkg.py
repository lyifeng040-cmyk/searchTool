import os,sys
print('cwd=', os.getcwd())
print('sys.path[0]=', sys.path[0])
print('\nfilesearch exists:', os.path.isdir('filesearch'))
print('filesearch contents:', sorted(os.listdir('filesearch')))
print('\nfilesearch/ui exists:', os.path.isdir(os.path.join('filesearch','ui')))
print('filesearch/ui contents:', sorted(os.listdir(os.path.join('filesearch','ui'))))
print('\nfilesearch/ui/components exists:', os.path.isdir(os.path.join('filesearch','ui','components')))
print('filesearch/ui/components contents:', sorted(os.listdir(os.path.join('filesearch','ui','components'))))
