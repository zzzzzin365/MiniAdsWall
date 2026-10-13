import { useEffect, useState } from 'react';
import { getMaterials, deleteMaterial, Material } from '../api';
import { Ad } from '../types';
import './MaterialLibrary.css';
import { usePerformance } from '../contexts/PerformanceContext';
import { materialPerformance } from '../utils/performance';
export default function MaterialLibrary({revision,onReuse}:{revision:number;onReuse:(ad:Ad)=>void}) {
 const {range,period}=usePerformance();
 const [items,setItems]=useState<Material[]>([]),[cursor,setCursor]=useState<string|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[query,setQuery]=useState('');
 const [searchInput,setSearchInput]=useState(''),[materialType,setMaterialType]=useState('all');
 const [deleting,setDeleting]=useState<Material|null>(null),[deleteBusy,setDeleteBusy]=useState(false);
 const editMaterial=(material:Material)=>{const url=new URL(window.location.href);url.searchParams.delete('marketing');url.searchParams.delete('studio');url.searchParams.set('edit_material',material.id);url.hash='creatives';window.history.pushState(null,'',url);window.dispatchEvent(new PopStateEvent('popstate'));window.scrollTo(0,0);};
 const confirmDelete=async()=>{if(!deleting||deleteBusy)return;setDeleteBusy(true);setError('');try{await deleteMaterial(deleting.id);setItems(old=>old.filter(item=>item.id!==deleting.id));setDeleting(null);}catch(e){setError(e instanceof Error?e.message:'删除失败，请重试');}finally{setDeleteBusy(false);}};
 const clearSearch=()=>{setSearchInput('');setQuery('');};
 useEffect(()=>{const c=new AbortController();setItems([]);setBusy(true);setError('');getMaterials(undefined,c.signal).then(p=>{setItems(p.items);setCursor(p.next_cursor);}).catch(()=>{if(!c.signal.aborted)setError('素材读取失败');}).finally(()=>{if(!c.signal.aborted)setBusy(false);});return()=>c.abort();},[revision]);
 async function more(){setBusy(true);setError('');try{const p=await getMaterials(cursor || undefined);setItems(old=>cursor?[...old,...p.items]:p.items);setCursor(p.next_cursor);}catch{setError('素材读取失败，请重试');}finally{setBusy(false);}}
 const filtered=items.filter(m=>(m.name+' '+m.content).toLowerCase().includes(query.toLowerCase())&&(materialType==='all'||(materialType==='video'?m.videos.length>0:m.videos.length===0)));
 return <section className="material-library" aria-label="素材库" aria-busy={busy}>
  <div className="management-toolbar material-library-toolbar">
   <button className="btn btn-primary" type="button" onClick={()=>editMaterial({id:'new'} as Material)}>+ 上传素材</button>
   <form className="ad-search" role="search" aria-label="素材搜索" onSubmit={event=>{event.preventDefault();setQuery(searchInput.trim());setSearchInput(searchInput.trim());}}>
    <div className="ad-search-input">
     <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>
     <input type="search" aria-label="搜索已加载素材" placeholder="搜索素材名称、正文" maxLength={200} value={searchInput} onChange={event=>{setSearchInput(event.target.value);if(!event.target.value)setQuery('');}}/>
     {(searchInput||query)&&<button className="ad-search-clear" type="button" aria-label="清空素材搜索" onClick={clearSearch}>×</button>}
    </div>
    <button className="btn btn-default" type="submit">搜索</button>
   </form>
   <label className="management-filter">素材类型<select aria-label="素材类型" value={materialType} onChange={event=>setMaterialType(event.target.value)}><option value="all">全部素材</option><option value="video">含视频</option><option value="link">图文 / 链接</option></select></label>
  </div>
  {error && <p role="alert">{error}<button onClick={()=>void more()}>重试</button></p>}
  <div className="management-creatives">{filtered.map(m=>{const metrics=materialPerformance(m.usage_ads || [],range);return <article className="material-card" key={m.id}>
   <header className="material-card-heading"><span className="material-type-tag">{m.videos.length?'视频素材':m.images?.length?'图片素材':'图文素材'}</span><h2 title={m.name}>{m.name}</h2><div className="material-card-meta"><span title={m.id}>ID：{m.id.slice(0,12)}</span><span>{m.usage_count} 条广告使用</span></div></header>
   <div className="material-card-preview">{m.videos.length?<video src={m.videos[0]} controls preload="metadata" aria-label={m.name+'视频预览'}/>:m.images?.length?<img src={m.images[0]} alt={m.name+'图片预览'}/>:<div className="material-text-preview"><span aria-hidden="true">T</span><p>{m.content||'暂无文案'}</p></div>}</div>
   <section className="material-copy"><h3>文案预览</h3><p>{m.content||'暂无文案'}</p>{(m.content||m.videos.length>1||(m.images?.length||0)>1)&&<details><summary>查看完整文案{m.videos.length>1?`与全部 ${m.videos.length} 个视频`:''}</summary><div className="material-full-copy">{m.content}</div>{m.images?.slice(m.videos.length?0:1).map((url,i)=><img key={url+i} src={url} alt={`图片素材 ${i+1}`}/>)}{m.videos.slice(1).map((url,i)=><video key={url+i} src={url} controls preload="none"/>)}</details>}</section>
   <dl className="material-performance" aria-label="关联广告投放效果合计"><dt>{period} · 关联广告合计（模拟）</dt><div><dd><strong>{metrics.impressions.toLocaleString('zh-CN')}</strong><span>曝光</span></dd><dd><strong>{metrics.clicks.toLocaleString('zh-CN')}</strong><span>点击</span></dd><dd><strong>{metrics.orders}</strong><span>订单</span></dd></div><p>花费 ¥{(metrics.spend/100).toLocaleString('zh-CN')} · 净收入 ¥{(metrics.revenue/100).toLocaleString('zh-CN')}</p></dl>
   <footer className="material-card-actions"><button className="btn btn-primary" onClick={()=>onReuse({id:'',title:m.name,publisher:'',content:m.content,videos:m.videos,url:'',price:1,clicks:0,version:1,material_id:m.id})}>使用素材新建广告</button><div><button className="btn btn-default" onClick={()=>editMaterial(m)} aria-label={'编辑素材：'+m.name}>编辑素材</button><button className="btn btn-default material-delete" onClick={()=>setDeleting(m)} aria-label={'删除素材：'+m.name}>删除</button></div></footer>
  </article>})}</div>
  {deleting&&<div className="modal-overlay active"><div className="modal-box" role="dialog" aria-modal="true" aria-labelledby="delete-material-title" onKeyDown={event=>{if(event.key==='Escape'&&!deleteBusy){setDeleting(null);setError('');}}}><div className="modal-header" id="delete-material-title">删除素材</div><div className="modal-body"><p>确定将“{deleting.name}”移出素材库？已使用此素材的广告保留原内容。</p>{error&&<p role="alert">{error}</p>}</div><div className="modal-footer"><button autoFocus className="btn btn-default" disabled={deleteBusy} onClick={()=>{setDeleting(null);setError('');}}>取消</button><button className="btn btn-danger" disabled={deleteBusy} onClick={()=>void confirmDelete()}>{deleteBusy?'删除中…':'确认删除'}</button></div></div></div>}
  {!busy && !error && items.length>0 && !filtered.length && <p>没有符合搜索或筛选条件的已加载素材。</p>}
  {!busy && !error && !items.length && <p>暂无素材，保存第一条广告后自动入库。</p>}
  {busy && <p role="status">正在加载素材…</p>}{cursor && <button className="btn btn-default" disabled={busy} onClick={()=>void more()}>加载更多素材</button>}
 </section>;
}
