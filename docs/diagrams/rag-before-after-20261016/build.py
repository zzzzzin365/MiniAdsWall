from pathlib import Path
import xml.etree.ElementTree as E
p=Path(__file__).parent
def sub(node, tag, **kw):
 attrs=kw.pop('attrib', {})
 attrs.update(kw)
 return E.SubElement(node, tag, attrs)
m=E.Element('mxfile',host='app.diagrams.net',agent='Codex',version='26.0.9'); d=sub(m,'diagram',id='rag-compare',name='优化前后'); g=sub(d,'mxGraphModel',page='1',pageWidth='1440',pageHeight='1135',grid='1',gridSize='10'); r=sub(g,'root');sub(r,'mxCell',id='0');sub(r,'mxCell',id='1',parent='0')
font='PingFang SC'
def box(i,t,x,y,w,h,fill='#FFFFFF',stroke='#CBD5E1',size=19,bold=False,dash=False):
 s=f'rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};strokeWidth=1.5;fontFamily={font};fontSize={size};fontColor=#172B4D;spacing=10;'
 if bold:s+='fontStyle=1;'
 if dash:s+='dashed=1;dashPattern=6 4;'
 c=sub(r,'mxCell',id=i,value=t.replace('\n','<br>'),style=s,vertex='1',parent='1');sub(c,'mxGeometry',x=str(x),y=str(y),width=str(w),height=str(h),attrib={'as':'geometry'})
def txt(i,t,x,y,w,h,size=18,color='#475569',bold=False,align='left'):
 s=f'text;html=1;whiteSpace=wrap;fillColor=none;strokeColor=none;fontFamily={font};fontSize={size};fontColor={color};align={align};verticalAlign=middle;spacing=0;'
 if bold:s+='fontStyle=1;'
 c=sub(r,'mxCell',id=i,value=t.replace('\n','<br>'),style=s,vertex='1',parent='1');sub(c,'mxGeometry',x=str(x),y=str(y),width=str(w),height=str(h),attrib={'as':'geometry'})
def edge(i,a,b,label='',points=None,extra=''):
 c=sub(r,'mxCell',id=i,value=label,style=f'edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;strokeWidth=1.8;strokeColor=#64748B;fontFamily={font};fontSize=16;labelBackgroundColor=#FFFFFF;'+extra,edge='1',parent='1',source=a,target=b); z=sub(c,'mxGeometry',relative='1',attrib={'as':'geometry'})
 if points:
  ar=sub(z,'Array',attrib={'as':'points'})
  for x,y in points:sub(ar,'mxPoint',x=str(x),y=str(y))

txt('title','多模态 RAG：优化前 → 优化后',40,24,1300,50,32,'#172B4D',True)
txt('sub','新增算法／方法已标在节点内：原生图像匹配、同源关联、字段选择、按需补读、确定性校验。',40,80,1340,34,20)

txt('oldtitle','优化前｜图片主要靠描述检索，证据直接交给模型',48,151,1320,36,23,'#475569',True)
old=[('o1','图片 → 文字描述\n文档 → 文本',48,230),('o2','文本向量索引\n按描述找图片',318,230),('o3','问题 → 检索\n取回候选',588,230),('o4','描述＋来源上下文\n直接交给 LLM',858,230),('o5','生成回答\n返回引用',1158,210)]
for i,t,x,w in old:box(i,t,x,212,w,76,'#FFFFFF','#CBD5E1',19)
for n in range(1,5):edge('oe'+str(n),'o'+str(n),'o'+str(n+1))
txt('oldnote','主要问题：描述可能漏细节；上下文冗余；已有证据不足时，答案和引用仍容易不合规。',48,305,1300,30,18)

txt('newtitle','优化后｜原生图像检索已实现；绿色虚线为实验方案，尚未接入业务',48,389,1330,38,23,'#245894',True)
box('index','算法：Chinese-CLIP 图文双编码器（512维）＋归一化向量的余弦近邻匹配<br>离线：原图 → 图像向量；文档 → 文本向量（独立索引）｜保留视觉观察、原图哈希与来源 ID',48,439,1320,64,'#FFFFFF','#93B4DC',18)
box('q','用户问题',48,547,120,90,'#FFFFFF','#93B4DC',20)
box('retr','图像／文本检索\n余弦匹配\n按排名交替合并',207,547,170,90,'#FFFFFF','#93B4DC',19)
box('exp','',401,516,780,280,'none','#4F9D81',dash=True)
box('assemble','① 同源证据组装\nSHA-256 哈希校验\n按清单关联来源',421,547,210,90,'#EAF6EF','#4F9D81',19)
box('cover','② 字段选择＋覆盖规则\n按题型选择字段\n缺项才触发补读',670,547,220,90,'#EAF6EF','#4F9D81',19)
box('answer','③ LLM 生成\n④ 字段类型／引用校验\n原文片段确定性截取',929,547,232,90,'#EAF6EF','#4F9D81',19)
box('out','带引用回答',1220,547,148,90,'#FFFFFF','#93B4DC',20)
edge('ne1','q','retr');edge('ne2','retr','assemble');edge('ne3','assemble','cover');edge('ne4','cover','answer');txt('covered','已覆盖',885,519,78,25,16,'#297456',False,'center');edge('ne5','answer','out')
box('read','规则触发视觉补读<br>最多一次<br>仍无证据则拒答',670,700,220,75,'#FFF4E5','#C99037',18)
edge('need','cover','read','缺视觉细节',extra='exitX=0.5;exitY=1;entryX=0.5;entryY=0;')
edge('back','read','answer','补充证据',points=[(1045,738)],extra='exitX=1;exitY=0.5;entryX=0.5;entryY=1;')
txt('retrieve-note','召回收益待独立验证',182,667,220,36,17,'#245894',False,'center')
txt('assembly-note','Token −18.2%\n质量未证实提升',419,670,215,58,17,'#297456',False,'center')
txt('results-title','实验实际带来了什么',40,815,1300,35,23,'#172B4D',True)
box('res1','已有字段不足 → 按需补读\n字段完成 37/84 → 66/84\n代价：P95 3.60 → 7.39 秒',40,865,425,108,'#FFFFFF','#CBD5E1',19)
box('res2','程序校验＋原文截取\n字段完成且合规 66/84 → 84/84\n新增 18 题均来自确定性摘录',505,865,425,108,'#FFFFFF','#CBD5E1',19)
box('res3','整套方案 vs 完整上下文基线\n在线费用估算 −43.1%\nP95 ＋75.8%：没有质量／速度双赢',970,865,430,108,'#FFF4E5','#C99037',19)
txt('foot','口径：21 张已知公开图、84 道封闭字段题；固定候选，未测检索召回。84/84 不代表通用问答准确率。',40,989,1360,28,16)
txt('scope','费用为在线估算；P95 为证据准备＋回答耗时，不含真实检索。上方前后架构与下方实验对照分别展示。',40,1027,1360,28,16)
txt('method-note','说明：这些是模型接入、检索策略和工程规则；没有训练新模型，也没有复现 GraphRAG。',40,1065,1360,28,16)
E.indent(m);E.ElementTree(m).write(p/'rag-before-after.drawio',encoding='utf-8',xml_declaration=True)
