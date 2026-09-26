import json, pathlib
d=json.load(open('data/_level_act16side_ex02.json',encoding='utf-8'))
T=d['mapData']['tiles']; M=d['mapData']['map']; H=len(M)
CT=json.load(open('data/_char_table.json',encoding='utf-8'))
POS={v['name']:v.get('position') for v in CT.values()}
def bt(x,yr): return T[M[H-1-yr][x]]['buildableType']
# v10：换编队——芬(8费)开局立刻堵右漏斗第一波；玫兰莎(13费)堵左；斥罪(34费)出队
plan=[("桃金娘",(1,1),"Up",1),("芬",(8,2),"Down",1),("玫兰莎",(1,3),"Up",1),
      ("古米",(8,3),"Down",1),("凯尔希",(9,3),"Right",3),("逻各斯",(9,2),"Right",1),
      ("黍",(8,4),"Down",3),("圣聆初雪",(9,4),"Right",2)]
bad=0
for n,(x,yr),dr,s in plan:
    need=POS.get(n); b=bt(x,yr)
    ok=(b=="ALL") or (need=="MELEE" and b=="MELEE") or (need=="RANGED" and b=="RANGED")
    bad+= 0 if ok else 1
    print(f"  {'OK ' if ok else '!! '}{n:<8}({need:<6})[{x},{yr}] {dr:<6}S{s} {b}")
print("不匹配:",bad); assert bad==0
job={"version":3,"stage_name":"act16side_ex02","minimum_required":"v4.0.0",
 "doc":{"title":"act16side_ex02〔编排v10·低费开局〕",
  "details":"斥罪(34费)出队→芬(8费)/玫兰莎(13费)入队。T+3s 首波从右侧来，芬8费立即落[8,2]堵漏斗；左路玫兰莎13费；部署序按费用升序"},
 "opers":[{"name":n,"skill":s,"skill_usage":1} for n,_,_,s in plan],"groups":[],
 "actions":[{"type":"SpeedUp"}]+[{"type":"Deploy","name":n,"location":[p[0],p[1]],"direction":dr} for n,p,dr,_ in plan]+[{"type":"SkillDaemon"}]}
out=pathlib.Path('data/copilot/ga_derived/act16side_ex02_derived_v10.json')
out.write_text(json.dumps(job,ensure_ascii=False,indent=1),encoding='utf-8'); print("→",out)
