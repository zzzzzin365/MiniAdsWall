# Visual Spec
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
