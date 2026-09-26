import json, pathlib
d=json.load(open('data/_level_act16side_ex02.json',encoding='utf-8'))
T=d['mapData']['tiles']; M=d['mapData']['map']; H=len(M); W=len(M[0])
CT=json.load(open('data/_char_table.json',encoding='utf-8'))
POS={v['name']:v.get('position') for v in CT.values()}
def tile(x,yr): return T[M[H-1-yr][x]]
plan=[("桃金娘",(1,1),"Up",1),("斥罪",(1,4),"Up",1),("古米",(1,3),"Up",1),
      ("圣聆初雪",(5,2),"Left",2),("逻各斯",(5,4),"Right",1),
      ("乌尔比安",(8,3),"Down",2),("黍",(8,2),"Down",3),("凯尔希",(9,3),"Left",3)]
print("点位/职业双向校验：")
bad=0
for n,(x,yr),dr,s in plan:
    t=tile(x,yr); bt=t['buildableType']; need=POS.get(n)
    ok = (bt=="ALL") or (need=="MELEE" and bt=="MELEE") or (need=="RANGED" and bt=="RANGED")
    bad += 0 if ok else 1
    print(f"  {'OK ' if ok else '!! '}{n:<8}({need:<6}) [{x},{yr}] {dr:<6} S{s}  格子={bt}/{t['tileKey']}")
print("不匹配:", bad); assert bad==0
job={"version":3,"stage_name":"act16side_ex02","minimum_required":"v4.0.0",
 "doc":{"title":"act16side_ex02〔编排v7·格子+职业双校验〕",
  "details":"col8 漏斗(5 route→右下(10,5)上行至右上蓝门(10,1))朝Down；col1 漏斗(r6家族10 route→左下门)朝Up；远程位只放远程，地面位只放近战"},
 "opers":[{"name":n,"skill":s,"skill_usage":1} for n,_,_,s in plan],
 "groups":[],
 "actions":[{"type":"SpeedUp"}]+[{"type":"Deploy","name":n,"location":[p[0],p[1]],"direction":dr} for n,p,dr,_ in plan]+[{"type":"SkillDaemon"}]}
out=pathlib.Path('data/copilot/ga_derived/act16side_ex02_derived_v7.json')
out.write_text(json.dumps(job,ensure_ascii=False,indent=1),encoding='utf-8')
print("→",out)
