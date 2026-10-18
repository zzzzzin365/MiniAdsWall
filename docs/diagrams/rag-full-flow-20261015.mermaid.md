# MiniAdsWall：当前 RAG 全流程与实验位置

截至2026-10-15；实线为当前代码链路，橙色虚线为隔离实验。代码接入不等于生产验收。

```mermaid
flowchart TB
  subgraph OFF["① 入库与索引"]
    direction LR
    P["通用知识／外部文章与案例图"] --> T["正文、图片描述分块"] --> V["文本 Embedding"] --> K["Chroma 公共知识索引"]
    M["本品牌图片／视频"] --> S["Seed 理解图片／最多8帧<br/>不含音频"] --> D["MySQL 保存理解结果<br/>素材版本、帧时间"] --> MV["描述分块＋文本向量化"] --> MK["Chroma 素材描述索引"]
  end
  subgraph ON["② 在线检索与回答 · 两条独立链路"]
    direction TB
    subgraph PUBLIC["公共助手／投放分析"]
      direction LR
      Q["用户问题"] --> RW["查询改写<br/>最多4路并行"] --> R["向量召回、去重"] --> RR["LLM 重排"] --> G["证据检查<br/>不足则澄清"] --> A["Agent 回答＋知识来源"]
    end
    subgraph STUDIO["商家创意工作台"]
      direction LR
      SQ["问题＋商品信息"] --> AUTH["校验品牌、环境、版本"] --> SR["描述检索<br/>外部Top3＋本品牌Top5"] --> CTX["组装已有图片／帧描述"] --> LLM["DeepSeek 生成草稿"] --> CITE["校验引用ID<br/>展示原图／视频时间点"]
    end
  end
  K --> R
  K --> SR
  MK --> SR
  subgraph EXP["③ 隔离实验 · 橙色虚线不代表已接入业务"]
    direction LR
    E1["E1 公开检索<br/>词法＋向量融合／查询向量缓存"]
    E2["E2 合成库<br/>HNSW参数／二值粗筛＋浮点精排"]
    subgraph VISION["E3／E4：检索后、回答前的视觉证据准备"]
      direction LR
      O["复用已保存观察"] --> ENOUGH{"信息够吗？"}
      ENOUGH -->|够| SEND["交给回答模型"]
      ENOUGH -->|缺细节| READ["Seed 补读原图"] --> UPDATE["更新观察"] --> SEND
    end
  end
  R -.实验位置.-> E1
  V -.实验位置.-> E2
  CTX -.实验插入位置.-> O
  classDef solid fill:#e9f2ff,stroke:#658bc9,color:#172538;
  classDef online fill:#eaf3e6,stroke:#83b45d,color:#172538;
  classDef db fill:#fff0d7,stroke:#e7a53c,color:#172538;
  classDef generation fill:#fbe4e0,stroke:#d88880,color:#172538;
  classDef experiment fill:#fff5e6,stroke:#e59b2f,stroke-dasharray:6 4,color:#513616;
  class P,T,V,M,S,D,MV solid;
  class Q,RW,R,RR,G,SQ,AUTH,SR,CTX,CITE online;
  class K,MK db;
  class A,LLM generation;
  class E1,E2,O,ENOUGH,READ,UPDATE,SEND experiment;
  style OFF fill:#fbfdff,stroke:#658bc9,stroke-width:2px
  style ON fill:#fcfefa,stroke:#83b45d,stroke-width:2px
  style EXP fill:#fffcf6,stroke:#e59b2f,stroke-dasharray:6 4
```

## 实验结果

```mermaid
flowchart LR
  E1["E1 公开检索 · 已完成<br/>43片段，新措辞30道可回答题<br/>语义 Recall@5：51.67% → 76.67%<br/>50%受控缓存命中：检索P95 458 → 274ms<br/>全新查询未证明稳定提速"]
  E2["E2 合成搜索 · 已完成<br/>10万随机向量：高召回HNSW更慢<br/>3600万二值粗筛＋精排<br/>8查询整批：114.64 → 66.03s<br/>邻居Recall@5仅62.5%，不采用"]
  E3["E3 原图补读 · 已完成<br/>4张商家图、20题<br/>每题读图 vs 关键词按需读图<br/>字段＋来源通过率均93.75%<br/>读图20→12次，P95 26.24→29.57s"]
  E4["E4 观察复用 · 部分完成<br/>6张外部图，完成121/240题次<br/>24题配对：每题读图 vs 缺项补读<br/>字段＋来源通过率90%→75%<br/>P95 10.29→6.39s，读图24→2次<br/>质量未达标，用量上限中止"]
  classDef result fill:#f5f8fc,stroke:#7893b3,color:#172538;
  class E1,E2,E3,E4 result;
```

E1的混合检索与查询向量缓存未部署；业务原有查询改写已存在。早期改写小试的来源Recall@3为77.08%→72.92%，与E1正式语料口径不同。

E4四组主比较（自动字段＋必要来源诊断，未人工盲审）：

| 方案 | 通过率 | 回答P95 |
| --- | ---: | ---: |
| A 仅描述 | 40% | 1.90秒 |
| B 每题读图 | 90% | 10.29秒 |
| C0 仅复用观察 | 75% | 3.09秒 |
| C1 缺项才补读 | 75% | 6.39秒 |

E4固定正确参考图，不证明向量召回提高。各实验指标不同：语义召回、最近邻召回、回答字段通过率不可混用；批耗时不是单请求P95。初始理解成本在报告单列。

待执行：SQ8＋浮点精排只有方案；56张外部手串图已校验、暂定分组，尚未跑实验。

原图的位图编辑两次网络失败，未生成可交付的修改后PNG；此文件是按原图分区重新绘制的可编辑流程图。

[源码与实验依据](rag-full-flow-20261015.md)

