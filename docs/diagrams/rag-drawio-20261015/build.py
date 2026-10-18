from pathlib import Path
import xml.etree.ElementTree as E
from html import escape
from copy import deepcopy
P=Path(__file__).resolve().parent
# Source reference colors sampled from its actual RGB histogram.
(P/'diagram-brief.md').write_text('''# Diagram Brief
## User Goal
Deliver a fully editable two-page .drawio: current RAG flow, experimental locations and measurements.
## Source Inventory
Original 862x602 flowchart: layout/style only. Prior verified code and reports: content and structure. See ../rag-full-flow-20261015.md.
## Requirement Traceability
Offline and online bands retained; public assistant and studio separated; experimental routes orange dashed; all nodes/text/arrows editable.
## Semantic Model
Solid black arrows are business data flow. Gray dashed arrows read an index. Orange dashed shapes and arrows are experimental, not deployed.
## Style Contract
Sampled palette: white #ffffff, blue #dae8fc/#6c8ebf, green #d5e8d4/#82b366, orange #ffe6cc/#d79b00, red #f8cecc/#b85450, purple #e1d5e7. Reference body approximately 12px, headings 15px; enlarged target uses title32, heading24, body18, footnote16. Rounded rect corners12px, strokes1.5px, black classic arrows2px; no shadows/icons. Outer margin30, row gap60, horizontal gap60. Original major frames solid; target frames dashed to distinguish regions from content and support geometry checking, color still identifies each stage. This is a new project-specific redraw, not a pixel replica.
## Open Assumptions
Public model name left generic Agent; studio remains DeepSeek. Experiment E4 uses Seed mini for all model roles; not a production model swap.
''')
(P/'visual-spec.md').write_text('''# Visual Spec
## Source
Original screenshot: /var/folders/yb/qd2mvkfj4q1_60f7f_f30v_r0000gn/T/codex-clipboard-0b39386e-f0b2-4527-b2ad-039162573d51.png. Target rag-full-flow.drawio; two pages, 1800x1380 and 1800x1300; font PingFang SC.
## Global Style
White background; palette and fonts in diagram-brief.md. Classic black data arrows, gray dashed index reads, orange dashed experiment arrows.
## Regions
Page1: offline (30,120,1740,330), online (30,490,1740,400), experiments (30,940,1740,340). Page2: four experiment panels, two columns.
## Text Blocks
Title (30,25,1740,50); section titles left aligned24; all node labels18; experimental cards18 and metric table18. No long opaque raster text.
## Shapes
Rounded rects for operations; cylinder for Chroma; diamond for sufficiency decision. All editable mxCells.
## Connectors
Left-to-right data. Index reads route through outer right gutter. Experimental branch enough->answer, missing->read->update->answer.
## Semantic Relations And Flow
Public docs->embedding->public Chroma; brand media->Seed->MySQL->text embedding->brand Chroma. Public retrieval includes rewrite/rerank/evidence. Studio caption retrieval includes scope/citations, not the public reranker. Experiment follows retrieval and precedes answer.
## Icons And Images
No icon assets; no embedded bitmap; no outside imagery.
''')
(P/'layout-grid.md').write_text('''# Layout Grid
## Canvas
Page1 1800x1380; page2 1800x1300. Margin30, baseline grid10.
## Grid Lines
Six-column pipeline x=60,340,620,900,1180,1460; node width220, gap60. Offline rows y=200,330; online rows y=590,750.
## Region Boxes
Offline30,120,1740,330; online30,490,1740,400; experiments30,940,1740,340. Results panels x30/920 y120/590, width850 height420.
## Repeated Components
6 pipeline nodes per row, 220x80. Result card text rows28 to34px high.
## Drawing Order
Regions, titles, nodes, connectors, captions. All geometry explicit.
''')
(P/'asset-ledger.md').write_text('''# Asset Ledger
## Exact Assets
No external icon/logo required. Original screenshot is a style reference only, not embedded.
## Editable Primitive Icons
Cylinder databases and diamond decision are draw.io primitives.
## Approximations
Typeface PingFang SC; original generic sans-serif was not identifiable. Enlargement changes font sizes intentionally.
## Missing Assets
None. All requested content is text, boxes and connectors.
''')
if not (P/'defect-log.md').exists(): (P/'defect-log.md').write_text('# Defect Log\n\nCanvas-only screenshot review and fixes will be recorded below.\n')
COL={'blue':('#dae8fc','#6c8ebf'),'green':('#d5e8d4','#82b366'),'orange':('#ffe6cc','#d79b00'),'red':('#f8cecc','#b85450'),'purple':('#e1d5e7','#9673a6'),'white':('#ffffff','#6c8ebf')}
mx=E.Element('mxfile',host='app.diagrams.net',agent='Codex',version='26.0.9',pages='2')
roots=[]
def page(pid,name,h):
 d=E.SubElement(mx,'diagram',id=pid,name=name); g=E.SubElement(d,'mxGraphModel',dx='1800',dy=str(h),grid='1',gridSize='10',guides='1',tooltips='1',connect='1',arrows='1',fold='1',page='1',pageScale='1',pageWidth='1800',pageHeight=str(h),math='0',shadow='0',background='#ffffff'); r=E.SubElement(g,'root'); E.SubElement(r,'mxCell',id='0'); E.SubElement(r,'mxCell',{'id':'1','parent':'0'}); roots.append((d,r)); return r
boxes={}
def node(r,id,text,x,y,w=220,h=80,color='blue',size=18,extra='',parent='1'):
 fill,stroke=COL[color]; style=f'rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};strokeWidth=1.5;fontFamily=PingFang SC;fontSize={size};fontColor=#152332;spacing=10;'+extra
 c=E.SubElement(r,'mxCell',{'id':id,'value':escape(text).replace('\n','<br>'),'style':style,'vertex':'1','parent':parent}); E.SubElement(c,'mxGeometry',x=str(x),y=str(y),width=str(w),height=str(h),**{'as':'geometry'}); boxes[id]=(x,y,w,h);return id

def text(r,id,s,x,y,w,h=32,size=18,color='#152332',bold=False):
 return node(r,id,s,x,y,w,h,size=size,extra=f'fillColor=none;strokeColor=none;align=left;spacing=0;fontColor={color};fontStyle={1 if bold else 0};')
def region(r,id,title,x,y,w,h,c):
 node(r,id,'',x,y,w,h,color=c,extra='fillColor=none;dashed=1;dashPattern=8 6;');text(r,id+'_title',title,x+25,y+16,w-320,36,24,COL[c][1],True)
def edge(r,id,a,b,points=None,label='',color='#000000',dashed=False,anchors=None):
 style=f'edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=classic;endFill=1;strokeWidth=1.7;strokeColor={color};fontFamily=PingFang SC;fontSize=16;labelBackgroundColor=#ffffff;'+('dashed=1;dashPattern=6 4;' if dashed else '')
 if anchors:
  ex,ey,ix,iy=anchors;style+=f'exitX={ex};exitY={ey};entryX={ix};entryY={iy};exitPerimeter=0;entryPerimeter=0;'
 c=E.SubElement(r,'mxCell',{'id':id,'value':label,'style':style,'edge':'1','parent':'1','source':a,'target':b});g=E.SubElement(c,'mxGeometry',relative='1',**{'as':'geometry'})
 if anchors:
  ax,ay,aw,ah=boxes[a];bx,by,bw,bh=boxes[b]
  E.SubElement(g,'mxPoint',x=str(ax+aw*ex),y=str(ay+ah*ey),**{'as':'sourcePoint'}); E.SubElement(g,'mxPoint',x=str(bx+bw*ix),y=str(by+bh*iy),**{'as':'targetPoint'})
 if points:
  arr=E.SubElement(g,'Array',**{'as':'points'})
  for px,py in points:E.SubElement(arr,'mxPoint',x=str(px),y=str(py))
def row(r,ids,labels,y,color='blue'):
 for i,(id,s) in enumerate(zip(ids,labels)):node(r,id,s,60+280*i,y,color=color)
 for a,b in zip(ids,ids[1:]):edge(r,a+'_to_'+b,a,b,anchors=(1,.5,0,.5))
r=page('flow','01 当前全流程与实验位置',1380)
text(r,'title','MiniAdsWall：当前 RAG 全流程与实验位置',30,25,1720,50,32,bold=True)
text(r,'legend','2026-10-15  |  实线：已接入代码  ·  橙色虚线：隔离实验，尚未接入业务',30,82,1720,28,18)
region(r,'offline','① 入库与索引（首次检索也可能补建）',30,120,1740,330,'blue')
row(r,['p','extract','chunk','emb','pubmeta','pubdb'],['通用知识／外部文章\n与案例图片','正文提取／\n归档图片描述','按文档或描述分块','文本 Embedding\nMiniLM · 384维','保留标题、来源链接\n和片段 ID','Chroma 公共知识索引'],200)
row(r,['media','seed','sql','caption','memb','mediadb'],['本品牌图片／视频','Seed 理解图片\n视频最多抽8帧，无音频','MySQL 保存理解结果\n版本与帧时间','主体、摘要、\n帧描述分块','文本 Embedding\n描述向量化','Chroma 素材描述索引'],330)
for id in ['pubdb','mediadb']:
 c=r.find(f"mxCell[@id='{id}']");c.set('style',c.get('style')+'shape=cylinder3;size=12;')
text(r,'public_collection','knowledge_base_cosine_v1',1445,285,280,28,13)
text(r,'media_collection','studio_media_caption_cosine_v1',1445,415,280,28,13)
region(r,'online','② 在线检索与回答：两条独立链路',30,490,1740,400,'green')
text(r,'public_label','公共助手／投放分析',60,546,540,30,20,bold=True)
row(r,['q','rewrite','recall','rerank','gate','answer'],['用户问题','查询改写\n最多4路并行','向量召回\n合并去重','LLM 重排\n保留 Top-K','证据检查\n不足则澄清','Agent 回答\n附知识来源'],590,'green')
text(r,'studio_label','商家创意工作台',60,705,540,30,20,bold=True)
row(r,['sq','auth','srecall','context','llm','cite'],['问题＋商品信息','校验品牌、环境\n与素材当前版本','检索描述\n外部Top3＋本品牌Top5','视觉证据准备〔E3/E4〕\n当前仅组装已有描述','DeepSeek\n生成可审阅草稿','校验引用 ID\n原图／视频时间点'],750,'green')
c=r.find("mxCell[@id='context']");c.set('style',c.get('style')+'fontSize=16;')
for id in ['answer','llm']:
 c=r.find(f"mxCell[@id='{id}']");c.set('style',c.get('style')+'fillColor=#f8cecc;strokeColor=#b85450;')
edge(r,'pub_read','pubdb','recall',[(1740,240),(1740,570),(730,570)],color='#6c8ebf',dashed=True,anchors=(1,.5,.5,0))
edge(r,'pub_studio','pubdb','srecall',[(1755,240),(1755,870),(760,870)],color='#6c8ebf',dashed=True,anchors=(1,.5,.64,1))
edge(r,'media_read','mediadb','srecall',[(1725,370),(1725,850),(700,850)],color='#82b366',dashed=True,anchors=(1,.5,.36,1))
region(r,'experiments','③ 实验位置（这里只画实验逻辑，不代表已经上线）',30,940,1740,340,'orange')
node(r,'e1','E1：公开知识召回环节\n词法＋向量融合／查询向量缓存',60,1010,530,70,'orange',extra='dashed=1;')
node(r,'e2','E2：向量搜索环节\nHNSW参数／二值粗筛＋浮点精排',620,1010,530,70,'orange',extra='dashed=1;')
text(r,'e34label','E3／E4：检索后、回答前\n固定参考图，测试视觉证据准备',1190,1010,510,70,20,'#8b6100',True)
node(r,'obs','复用已有画面观察',60,1170,250,70,'orange',extra='dashed=1;')
node(r,'enough','信息够吗？',370,1150,160,110,'orange',extra='shape=rhombus;rounded=0;dashed=1;')
node(r,'read','Seed 补读原图一次',610,1170,250,70,'orange',extra='dashed=1;')
node(r,'update','补充可复用观察',940,1170,250,70,'orange',extra='dashed=1;')
node(r,'send','交给回答模型',1370,1170,270,70,'orange',extra='dashed=1;')
for a,b in [('obs','enough'),('enough','read'),('read','update'),('update','send')]:edge(r,'exp_'+a,a,b,label='缺细节' if a=='enough' else '',color='#d79b00',dashed=True,anchors=(1,.5,0,.5))
edge(r,'enough_yes','enough','send',[(450,1120),(1505,1120)],label='信息已覆盖，直接回答',color='#d79b00',dashed=True,anchors=(.5,0,.5,0))
text(r,'foot','当前是文字／图片描述检索，不是原生图像向量检索；代码接入不等于生产验收通过。实验数据与结果见第2页。',40,1310,1720,38,18)
r=page('results','02 对照实验与结果',1300)
text(r,'rtitle','做了哪些实验？对照了什么？结果如何？',30,25,1720,50,32,bold=True)
text(r,'rlegend','各实验独立，不能拼成一次“全流程质量和速度双提升”。',30,82,1720,28,18)
def card(id,title,x,y,lines,c='blue',h=420):
 region(r,id,title,x,y,850,h,c)
 for n,s in enumerate(lines): text(r,id+'_line'+str(n),s,x+25,y+70+n*37,800,32,19)
card('c1','E1 公开检索｜已完成',30,120,[
 '数据：43个公开片段；新增措辞30道可回答题',
 '对照：纯向量 vs 向量＋标题／正文词法融合',
 '语义 Recall@5：51.67% → 76.67%',
 '50%受控缓存命中：检索 P95 458 → 274 ms',
 '全新查询未证明稳定提速；不含生成耗时',
 '实验优化未接入业务链路',
 '另：早期改写小试 Recall@3 77.08% → 72.92%',
 '改写小试与正式检索实验的数据及Top-K不同'])
card('c2','E2 合成库搜索／量化｜已完成',920,120,[
 '10万随机向量：HNSW 搜索宽度参数对照',
 '高召回配置在本机比精确扫描更慢',
 '3600万：float32 vs 二值粗筛＋浮点精排',
 '8查询整批耗时：114.64 → 66.03 秒',
 '最近邻 Recall@5：100% → 62.5%',
 '结论：提速但漏召回，不替换业务检索',
 '批耗时 ≠ 单请求 P95；不是广告语义召回'])
card('c3','E3 原图补读｜4张商家图，已完成',30,590,[
 '数据：4张商家图、20道正式问题',
 '对照：每题读图 H vs 关键词按需读图 R',
 '字段＋必要来源通过率：两组均93.75%',
 '在线读图：20 → 12次',
 '完整 P95：26.24 → 29.57秒',
 '结论：调用减少，尾延迟没有下降',
 '历史商家素材实验，与后续外部图实验分开',
 '小样本、自动字段诊断，不是生产验收'])
region(r,'c4','E4 观察复用＋缺项补读｜部分完成',920,590,850,420,'orange')
text(r,'c4scope','6张外部图；完成121/240题次，主比较24题配对',945,653,800,32,19)
text(r,'c4model','本轮回答、路由、视觉模型均为 Seed mini',945,689,800,30,18)
for n,(a,b,c) in enumerate([('方案','字段＋来源通过率','回答 P95'),('A 仅描述','40%','1.90秒'),('B 每题读图','90%','10.29秒'),('C0 仅复用观察','75%','3.09秒'),('C1 缺项才补读','75%','6.39秒')]):
 yy=733+n*35
 text(r,'t'+str(n)+'a',a,945,yy,320,30,18,bold=n==0);text(r,'t'+str(n)+'b',b,1265,yy,280,30,18,bold=n==0);text(r,'t'+str(n)+'c',c,1555,yy,180,30,18,bold=n==0)
text(r,'c4reads','C1 vs B：在线读图24 → 2次；通过率下降15个百分点',945,918,800,30,18)
text(r,'c4decision','质量未达标；用量上限中止；尚未人工盲审',945,960,800,30,18,'#8b6100',True)
region(r,'pending','未执行与解释边界',30,1060,1740,180,'orange')
text(r,'pending1','SQ8＋浮点精排：只有方案。56张外部手串图：已校验、暂定分组，尚未跑实验。',55,1115,1680,32,20)
text(r,'pending2','E4固定正确参考图，不证明检索召回提升；语义召回、最近邻召回、回答字段通过率是不同指标。',55,1157,1680,32,18)
text(r,'sources','依据：源码与报告见同目录 sources.md；所有框、文字、表格、箭头均可在 draw.io 编辑。',35,1260,1720,30,16)
E.indent(mx)
E.ElementTree(mx).write(P/'rag-full-flow.drawio',encoding='utf-8',xml_declaration=True)
for i,(d,_) in enumerate(roots,1):
 single=E.Element('mxfile',host='app.diagrams.net',pages='1');single.append(deepcopy(d));E.ElementTree(single).write(P/f'page{i}.drawio',encoding='utf-8',xml_declaration=True)
(P/'sources.md').write_text((P.parent/'rag-full-flow-20261015.md').read_text().replace('图片使用内置 imagegen 修改用户提供的流程图。完整生成提示词保存在同目录 `rag-full-flow-20261015.prompt.txt`。','本文件夹交付的是可编辑 draw.io XML；原图的位图编辑因网络错误未生成PNG。'))
print(P/'rag-full-flow.drawio')
