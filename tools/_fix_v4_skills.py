import json, pathlib
SKILL={"桃金娘":1,"凛御银灰":3,"圣聆初雪":2,"逻各斯":1,"古米":1,"乌尔比安":2,"凯尔希":3,"黍":3}
p=pathlib.Path('data/copilot/ga_derived/act16side_ex02_derived_v4_raid.json')
j=json.load(open(p,encoding='utf-8'))
for o in j["opers"]:
    o["skill"]=SKILL.get(o["name"],1)
    o["skill_usage"]=1
j["doc"]["title"]="act16side_ex02〔编排v4·技能修正〕"
j["doc"]["details"]=j["doc"]["details"]+"；技能：银灰S3产费/初雪S2积雪/逻各斯S1殁亡/乌尔比安S2/凯尔希S3真伤/黍S3"
out=pathlib.Path('data/copilot/ga_derived/act16side_ex02_derived_v4b.json')
out.write_text(json.dumps(j,ensure_ascii=False,indent=1),encoding='utf-8')
print(out)
for o in j["opers"]: print(f"  {o['name']:<8} skill={o['skill']} usage={o['skill_usage']}")
print("动作数:", len(j["actions"]))
