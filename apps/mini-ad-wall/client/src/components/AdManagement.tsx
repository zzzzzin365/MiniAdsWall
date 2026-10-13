import React, { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { usePerformance } from '../contexts/PerformanceContext';
import MaterialLibrary from './MaterialLibrary';
import DataDashboard from './DataDashboard';
import { sum } from '../utils/performance';
import { Ad } from '../types';
const CreativeEditor = lazy(() => import('./merchant/studio/MerchantStudio'));
export type ManagementView = 'list' | 'materials' | 'creatives' | 'analytics';

interface AdManagementProps {
    view: ManagementView;
    ads: Ad[];
    loading: boolean;
    error: string;
    query: string;
    listRevision: number;
    hasMore: boolean;
    toolbar: React.ReactNode;
    onRetry: () => void;
    onClearQuery: () => void;
    onLoadMore: () => Promise<boolean>;
    onEdit: (ad: Ad) => void;
    onCopy: (ad: Ad) => void;
    onDelete: (ad: Ad) => void;
    onDetail: (ad: Ad) => void;
    onPreview: (ad: Ad) => void;
    onReuseMaterial: (ad: Ad) => void;
}

type SortKey = 'default' | 'price' | 'clicks';
const PAGE_SIZE = 10;
const money = (value: number) => `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const videoCount = (ad: Ad) => ad.video_count ?? ad.videos?.length ?? 0;

function SelectionCheckbox({ partial, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { partial?: boolean }) {
    const ref = useRef<HTMLInputElement>(null);
    React.useEffect(() => { if (ref.current) ref.current.indeterminate = !!partial; }, [partial]);
    return <input {...props} type="checkbox" ref={ref} />;
}

export default function AdManagement(props: AdManagementProps) {
    const { ads, loading, error, query, listRevision, hasMore, toolbar, onRetry, onClearQuery, onLoadMore, onEdit, onCopy, onDelete, onDetail, onPreview } = props;
    const view = props.view;
    const {measures,range,period} = usePerformance();
    const [creativeOpened, setCreativeOpened] = useState(view === 'creatives');
    useEffect(() => { if (view === 'creatives') setCreativeOpened(true); }, [view]);
    const [material, setMaterial] = useState('all');
    const [priceRange, setPriceRange] = useState('all');
    const [sort, setSort] = useState<{ key: SortKey; direction: 'asc' | 'desc' }>({ key: 'default', direction: 'desc' });
    const [selected, setSelected] = useState<Set<string>>(() => new Set());
    const [page, setPage] = useState(1);
    const navigationRequest = useRef(0);

    React.useEffect(() => {
        setPage(1);
        navigationRequest.current++;
    }, [listRevision, material, priceRange, sort.key, sort.direction]);

    // Keep the server's ranking order for charts; table sorting only changes presentation.
    const filtered = useMemo(() => ads.filter(ad => {
        if (material === 'video' && !videoCount(ad)) return false;
        if (material === 'link' && videoCount(ad)) return false;
        return priceRange === 'all' || (priceRange === 'low' ? ad.price < 100 : ad.price >= 100);
    }), [ads, material, priceRange]);
    const rows = useMemo(() => sort.key === 'default' ? filtered : [...filtered].sort((a, b) => {
        const difference = sort.key === 'clicks' ? measures(a).clicks - measures(b).clicks : a.price - b.price;
        return (sort.direction === 'asc' ? difference : -difference) || a.id.localeCompare(b.id);
    }), [filtered, sort, range]);
    const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    const currentPage = Math.min(page, pageCount);
    const pageRows = rows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
    const selectedRows = rows.filter(ad => selected.has(ad.id));
    const selectedOnPage = pageRows.filter(ad => selected.has(ad.id));
    const allSelected = pageRows.length > 0 && selectedOnPage.length === pageRows.length;
    const totals = sum(filtered.map(measures));
    const totalClicks = filtered.reduce((sum, ad) => sum + measures(ad).clicks, 0);
    const averagePrice = filtered.length ? filtered.reduce((sum, ad) => sum + ad.price, 0) / filtered.length : null;
    const filtersActive = material !== 'all' || priceRange !== 'all';

    function toggleSelection(id: string) {
        setSelected(previous => { const next = new Set(previous); next.has(id) ? next.delete(id) : next.add(id); return next; });
    }
    function sortBy(key: 'price' | 'clicks') {
        setSort(previous => ({ key, direction: previous.key === key && previous.direction === 'desc' ? 'asc' : 'desc' }));
    }
    async function nextPage() {
        if (loading || error) return;
        if (currentPage < pageCount) { setPage(currentPage + 1); return; }
        if (!hasMore) return;
        const request = ++navigationRequest.current;
        if (await onLoadMore() && request === navigationRequest.current) setPage(currentPage + 1);
    }
    function exportRows() {
        const exported = selectedRows.length ? selectedRows : rows;
        // Treat spreadsheet formula prefixes as text, including after leading whitespace.
        const cell = (value: string | number) => {
            let text = String(value);
            if (/^\s*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
            return '"' + text.replace(/"/g, '""') + '"';
        };
        const csv = [['广告 ID', '广告名称', '发布者', '出价（元）', `有效点击（${period}）`, '视频素材数量', '素材 ID', '数据版本', '有效曝光', '归因订单', '花费（元）', '归因净收入（元）'],
            ...exported.map(ad => [ad.id, ad.title, ad.publisher, ad.price, measures(ad).clicks, videoCount(ad), ad.material_id || "", ad.version, measures(ad).impressions, measures(ad).orders, measures(ad).spend/100, measures(ad).revenue/100])]
            .map(row => row.map(cell).join(',')).join('\r\n');
        const url = URL.createObjectURL(new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' }));
        const link = document.createElement('a');
        link.href = url; link.download = `广告管理-${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(link);
        link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    return <>
        <section className="ad-management" aria-label={view === 'materials' ? '已有素材管理' : '广告管理列表'} hidden={view === 'analytics'}>
            <div className="management-toolbar" hidden={view !== 'list'}>
                {toolbar}
                <label className="management-filter">素材类型
                    <select value={material} onChange={event => setMaterial(event.target.value)}>
                        <option value="all">全部素材</option><option value="video">含视频</option><option value="link">图文 / 链接</option>
                    </select>
                </label>
                <label className="management-filter">出价区间
                    <select value={priceRange} onChange={event => setPriceRange(event.target.value)}><option value="all">不限</option><option value="low">低于 ¥100</option><option value="high">¥100 及以上</option></select>
                </label>
                <button type="button" className="management-text-button" onClick={() => { setMaterial('all'); setPriceRange('all'); }}>重置筛选</button>
                <button type="button" className="btn btn-default management-export" onClick={exportRows} disabled={!rows.length || loading || !!error}>{selectedRows.length ? `导出选中 (${selectedRows.length})` : '导出列表'}</button>
            </div>
            {view === 'list' && selectedRows.length > 0 && <div className="management-selection" role="status">
                <span>已选中 <strong>{selectedRows.length}</strong> 条广告</span>
                <button type="button" className="management-text-button" onClick={() => setSelected(new Set())}>取消选择</button>
            </div>}
            <div id="ad-management-content" aria-busy={view !== 'creatives' && loading}>
                {creativeOpened && <div className="creative-module" hidden={view !== 'creatives'}>
                    {creativeOpened && <Suspense fallback={<div className="ad-list-message" role="status">正在加载创意工具…</div>}><CreativeEditor /></Suspense>}
                </div>}
                <div hidden={view !== 'list'}>
                {error ? <div className="ad-list-message" role="alert">{error}<button type="button" className="btn btn-default" onClick={onRetry}>重试</button></div>
                    : !ads.length && loading ? <div className="ad-list-message" role="status">{query ? '正在搜索广告…' : '正在加载广告…'}</div>
                    : !rows.length ? <div className="management-empty">
                        <strong>{filtersActive ? '没有符合筛选条件的广告' : query ? `没有找到与“${query}”匹配的广告` : '暂无广告，请新建第一条广告'}</strong>
                        {filtersActive && <button type="button" className="btn btn-default" onClick={() => { setMaterial('all'); setPriceRange('all'); }}>重置筛选</button>}
                        {query && <button type="button" className="btn btn-default" onClick={onClearQuery}>查看全部广告</button>}
                    </div> : <div className="management-table-scroll" tabIndex={0} role="region" aria-label="广告表格，可横向滚动">
                        <table className="management-table">
                            <caption className="management-sr-only">已加载广告的名称、素材、出价和有效点击</caption>
                            <thead><tr>
                                <th className="management-checkbox-cell" scope="col"><SelectionCheckbox aria-label="选择本页全部广告" checked={allSelected} partial={!!selectedOnPage.length && !allSelected} onChange={() => setSelected(previous => {
                                    const next = new Set(previous); pageRows.forEach(ad => allSelected ? next.delete(ad.id) : next.add(ad.id)); return next;
                                })} /></th>
                                <th scope="col">广告名称</th><th scope="col">操作</th><th scope="col">素材配置</th>
                                <th scope="col" className="management-numeric" aria-sort={sort.key === 'price' ? sort.direction === 'asc' ? 'ascending' : 'descending' : 'none'}><button type="button" onClick={() => sortBy('price')}>出价（元） <span aria-hidden="true">{sort.key === 'price' ? sort.direction === 'desc' ? '↓' : '↑' : '↕'}</span></button></th>
                                <th scope="col" className="management-numeric" aria-sort={sort.key === 'clicks' ? sort.direction === 'asc' ? 'ascending' : 'descending' : 'none'}><button type="button" onClick={() => sortBy('clicks')}>有效点击 <span aria-hidden="true">{sort.key === 'clicks' ? sort.direction === 'desc' ? '↓' : '↑' : '↕'}</span></button></th>
                                <th scope="col" className="management-numeric">有效曝光</th><th scope="col" className="management-numeric">归因订单</th><th scope="col" className="management-numeric">广告花费</th><th scope="col" className="management-numeric">归因净收入</th>
                            </tr></thead>
                            <tbody>
                                <tr className="management-total-row"><td /><td>当前列表 · {rows.length} 条</td><td /><td>{filtered.reduce((sum, ad) => sum + videoCount(ad), 0)} 个视频素材</td><td className="management-numeric">均价 {averagePrice === null ? '—' : money(averagePrice)}</td><td className="management-numeric">{totalClicks.toLocaleString('zh-CN')}</td><td className="management-numeric">{totals.impressions.toLocaleString('zh-CN')}</td><td className="management-numeric">{totals.orders}</td><td className="management-numeric">{money(totals.spend/100)}</td><td className="management-numeric">{money(totals.revenue/100)}</td></tr>
                                {pageRows.map(ad => <tr key={ad.id} className={selected.has(ad.id) ? 'is-selected' : ''}>
                                    <td className="management-checkbox-cell"><input type="checkbox" aria-label={`选择广告：${ad.title}`} checked={selected.has(ad.id)} onChange={() => toggleSelection(ad.id)} /></td>
                                    <td className="management-name-cell"><button type="button" className="management-ad-name" onClick={() => onDetail(ad)}>{ad.title}</button><div className="management-ad-meta"><span>{ad.publisher}</span><span>ID：{ad.id}</span></div></td>
                                    <td><div className="management-row-actions"><button type="button" onClick={() => onEdit(ad)} aria-label={`编辑广告：${ad.title}`}>编辑</button><button type="button" onClick={() => onCopy(ad)} aria-label={`复制广告：${ad.title}`}>复制</button><details className="management-row-menu"><summary aria-label={`更多操作：${ad.title}`}>···</summary><div className="management-row-popover"><button type="button" onClick={event => { event.currentTarget.closest('details')?.removeAttribute('open'); onPreview(ad); }}>预览素材</button><button type="button" className="management-delete" onClick={event => { event.currentTarget.closest('details')?.removeAttribute('open'); onDelete(ad); }}>删除广告</button></div></details></div></td>
                                    <td><span className={`management-material-badge ${videoCount(ad) ? 'has-video' : ''}`}>{videoCount(ad) ? '视频素材' : '图文 / 链接'}</span><div className="management-ad-meta">{videoCount(ad) ? `${videoCount(ad)} 个视频` : '文案素材'}</div>{ad.material_id && <div className="management-ad-meta">素材：{ad.material_id.slice(0,12)}</div>}</td>
                                    <td className="management-numeric management-price">{money(ad.price)}</td><td className="management-numeric">{measures(ad).clicks.toLocaleString('zh-CN')}</td><td className="management-numeric">{measures(ad).impressions.toLocaleString('zh-CN')}</td><td className="management-numeric">{measures(ad).orders}</td><td className="management-numeric">{money(measures(ad).spend/100)}</td><td className="management-numeric">{money(measures(ad).revenue/100)}</td>
                                </tr>)}
                            </tbody>
                        </table>
                    </div>}
                </div>
                {view === 'materials' && <MaterialLibrary revision={listRevision} onReuse={props.onReuseMaterial} />}

            </div>
            <div className="management-list-footer" hidden={view !== 'list'}>
                <span role="status">{error ? '数据读取失败' : loading && !ads.length ? '正在读取数据…' : `${hasMore ? '已加载' : '共'} ${rows.length} 条${query ? '匹配广告' : '广告'} · 每页 ${PAGE_SIZE} 条`}</span>
                <nav className="management-pagination" aria-label="广告分页">
                    <button type="button" className="btn btn-default" disabled={currentPage === 1 || loading || !!error} onClick={() => setPage(currentPage - 1)}>上一页</button>
                    <span aria-live="polite">第 {currentPage} / {pageCount} 页{hasMore ? '（可继续加载）' : ''}</span>
                    <button type="button" className="btn btn-default" disabled={(!hasMore && currentPage === pageCount) || loading || !!error} onClick={() => void nextPage()}>{loading && ads.length ? '加载中…' : '下一页'}</button>
                </nav>
            </div>
        </section>
        {view === 'analytics' && <DataDashboard ads={filtered}
            scope={error ? <div className="ad-list-message" role="alert">{error}<button className="btn btn-default" onClick={onRetry}>重试</button></div>
                : loading && !ads.length ? <p role="status">正在加载广告数据…</p>
                : <p className="management-data-scope">统计范围：{query ? `搜索“${query}”后，` : ''}当前筛选的 {filtered.length} 条已加载广告。出价是配置值，点击与投放效果面板一致（{period}，模拟数据）。</p>}
            loadMore={hasMore ? <button className="btn btn-default" disabled={loading} onClick={() => void onLoadMore()}>{loading ? '加载中…' : '加载更多广告数据'}</button> : undefined}
        />}
    </>;
}
