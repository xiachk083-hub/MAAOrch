import json
for inst in ['maa/instances/1', 'maa/instances/2']:
    fn = f'{inst}/config/gui.new.json'
    d = json.load(open(fn, 'rb'))
    tq = d.get('Configurations',{}).get('Default',{}).get('TaskQueue',[])
    for item in tq:
        if item.get('TaskType') == 'Fight':
            print(f'{inst}: AnnihilationStage={item.get("AnnihilationStage")!r}')
