import os
matches=[]
for root,dirs,files in os.walk('.'):
    for f in files:
        if 'ui_builder' in f or 'event_handlers' in f or 'file_operations' in f or 'search_logic' in f:
            matches.append(os.path.join(root,f))
print('\n'.join(matches) or 'no matches')
