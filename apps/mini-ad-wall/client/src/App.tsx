import React, { useState, useEffect, useRef } from 'react';
import { getAdsPage, getAd, createAd, updateAd, deleteAd, setStudioCredential, hasStudioCredential } from './api';
import AdManagement, { ManagementView } from './components/AdManagement';
import AdDetailDrawer from './components/AdDetailDrawer';
import AdModal from './components/AdModal';
import DeleteModal from './components/DeleteModal';
import VideoModal from './components/VideoModal';
import AIAssistantPanel from './components/AIAssistantPanel';
import { Ad, AdInput, AdModalState, DeleteModalState, VideoModalState, ToastState } from './types';

const moduleInfo: Record<ManagementView, { title: string; description: string; hash: string }> = {
    list: { title: '广告管理', description: '管理广告、投放配置与素材', hash: 'ads' },
    materials: { title: '已有素材管理', description: '浏览已有广告素材，预览、编辑与复用', hash: 'materials' },
    creatives: { title: '素材生成工作台', description: '创造你的营销素材', hash: 'creatives' },
    analytics: { title: '数据分析', description: '查看广告数据、营销指标与效果报告', hash: 'data-analysis' },
};
const readModule = (): ManagementView => {
    const hash = window.location.hash.slice(1);
    if (hash === 'creatives' || new URLSearchParams(window.location.search).get('studio') === '1') return 'creatives';
    if (hash === 'materials') return 'materials';
    if (hash === 'data-analysis' || new URLSearchParams(window.location.search).get('marketing') === '1') return 'analytics';
    return 'list';
};
function NavIcon({ kind }: { kind: 'ads' | 'materials' | 'analytics' }) {
    return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        {kind === 'ads' ? <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>
            : kind === 'materials' ? <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M7 5V3h10v2M3 16l5-5 4 4 3-3 6 6" /><circle cx="16" cy="10" r="1" /></>
            : <><path d="M4 3v18h17M8 16v-5M13 16V7M18 16V4" /></>}
    </svg>;
}

function App() {
    const [profileOpen, setProfileOpen] = useState(false);
    const [credential, setCredential] = useState('');
    const [credentialSaved, setCredentialSaved] = useState(false);
    const [activeModule, setActiveModule] = useState<ManagementView>(readModule);
    const [materialsExpanded, setMaterialsExpanded] = useState(true);
    const [connected, setConnected] = useState(hasStudioCredential);
    const creativeActive = activeModule === 'creatives';
    useEffect(() => {
        const sync = () => setActiveModule(readModule());
        const syncCredential = () => setConnected(hasStudioCredential());
        window.addEventListener('hashchange', sync);
        window.addEventListener('studio-credential-changed', syncCredential);
        return () => { window.removeEventListener('hashchange', sync); window.removeEventListener('studio-credential-changed', syncCredential); };
    }, []);
    const navigate = (view: ManagementView) => {
        setActiveModule(view);
        const url = new URL(window.location.href);
        url.searchParams.delete('studio'); url.searchParams.delete('marketing');
        url.hash = moduleInfo[view].hash;
        if (window.location.href !== url.href) window.history.pushState(null, '', url);
        window.scrollTo({ top: 0, behavior: 'auto' });
    };
    useEffect(() => {
        const sync = () => setActiveModule(readModule());
        window.addEventListener('popstate', sync);
        return () => window.removeEventListener('popstate', sync);
    }, []);
    const [nextCursor, setNextCursor] = useState<string | null>(null);
    const [loadingPage, setLoadingPage] = useState(true);
    const [searchInput, setSearchInput] = useState('');
    const [searchQuery, setSearchQuery] = useState('');
    const [loadError, setLoadError] = useState('');
    const [listRevision, setListRevision] = useState(0);
    const queryRef = useRef('');
    const requestId = useRef(0);
    const requestController = useRef<AbortController | null>(null);
    const [ads, setAds] = useState<Ad[]>([]);
    const [adModal, setAdModal] = useState<AdModalState>({ open: false, mode: 'create', data: null });
    const [deleteModal, setDeleteModal] = useState<DeleteModalState>({ open: false, id: null });
    const [videoModal, setVideoModal] = useState<VideoModalState>({ open: false, url: '', adId: null, landingUrl: '' });
    const [toast, setToast] = useState<ToastState>({ show: false, msg: '' });
    const [detail, setDetail] = useState<{ ad: Ad; loading: boolean; error: string } | null>(null);
    const detailRequestId = useRef(0);

    useEffect(() => {
        loadAds();
        return () => { requestId.current++; detailRequestId.current++; requestController.current?.abort(); };
    }, []);

    const loadAds = async (query = queryRef.current) => {
        const id = ++requestId.current;
        setListRevision(id);
        requestController.current?.abort();
        const controller = new AbortController();
        requestController.current = controller;
        queryRef.current = query;
        setSearchQuery(query);
        setLoadingPage(true);
        setLoadError('');
        setAds([]);
        setNextCursor(null);
        try {
            const page = await getAdsPage(undefined, query, controller.signal);
            if (id !== requestId.current || controller.signal.aborted) return;
            setAds(page.items); setNextCursor(page.next_cursor);
        } catch (error) {
            if (id === requestId.current && !controller.signal.aborted) {
                setConnected(hasStudioCredential());
                setLoadError(query ? '搜索失败，请重试' : '广告读取失败，请重试');
            }
        } finally {
            if (id === requestId.current) setLoadingPage(false);
        }
    };

    const loadNextPage = async () => {
        if (!nextCursor || loadingPage) return false;
        const id = requestId.current;
        const controller = new AbortController();
        requestController.current = controller;
        setLoadingPage(true);
        try {
            const page = await getAdsPage(nextCursor, queryRef.current, controller.signal);
            if (id !== requestId.current || controller.signal.aborted) return false;
            setAds(previous => { const existing = new Set(previous.map(ad => ad.id)); return [...previous, ...page.items.filter(ad => !existing.has(ad.id))]; });
            setNextCursor(page.next_cursor);
            return true;
        }
        catch { if (id === requestId.current && !controller.signal.aborted) showToast('下一页读取失败，请重试'); return false; }
        finally { if (id === requestId.current) setLoadingPage(false); }
    };

    const handleSearch = (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const query = searchInput.trim();
        setSearchInput(query);
        void loadAds(query);
    };

    const clearSearch = () => {
        setSearchInput('');
        void loadAds('');
    };

    const showToast = (msg: string) => {
        setToast({ show: true, msg });
        setTimeout(() => setToast({ show: false, msg: '' }), 3000);
    };

    const handleCreateClick = () => {
        setAdModal({ open: true, mode: 'create', data: null });
    };

    const handleEditClick = async (ad: Ad) => {
        try { ad = await getAd(ad.id); } catch { showToast("广告详情读取失败"); return; }
        setAdModal({ open: true, mode: 'edit', data: ad });
    };

    const handleCopyClick = async (ad: Ad) => {
        try { ad = await getAd(ad.id); } catch { showToast("广告详情读取失败"); return; }
        setAdModal({ open: true, mode: 'copy', data: ad });
    };

    const handleDeleteClick = (ad: Ad) => {
        setDeleteModal({ open: true, id: ad.id });
    };

    const handleAdSubmit = async (formData: AdInput) => {
        try {
            if (adModal.mode === 'create') {
                await createAd(formData);
                showToast('广告创建成功');
            } else if (adModal.mode === 'copy') {
                await createAd(formData);
                showToast(`广告复制成功：${formData.title}`);
            } else if (adModal.mode === 'edit' && adModal.data) {
                await updateAd(adModal.data.id, { ...formData, version: adModal.data.version });
                showToast('广告更新成功');
            }
            setAdModal({ ...adModal, open: false });
            loadAds();
        } catch (error) {
            console.error("Operation failed", error);
            if ((error as any).response?.status === 409) await loadAds();
            alert(error instanceof Error ? error.message : "操作未完成，请重试");
        }
    };

    const handleConfirmDelete = async () => {
        try {
            if (deleteModal.id) {
                await deleteAd(deleteModal.id);
                showToast('广告已删除');
                setDeleteModal({ open: false, id: null });
                loadAds();
            }
        } catch (error) {
            console.error("Delete failed", error);
            alert(error instanceof Error ? error.message : "删除未完成，请重试");
        }
    };

    const handleDetailClick = async (ad: Ad) => {
        const id = ++detailRequestId.current;
        setDetail({ ad, loading: true, error: '' });
        try {
            const fullAd = await getAd(ad.id);
            if (id === detailRequestId.current) setDetail({ ad: fullAd, loading: false, error: '' });
        } catch {
            if (id === detailRequestId.current) setDetail({ ad, loading: false, error: '完整详情读取失败，请重试。' });
        }
    };

    const closeDetail = () => {
        detailRequestId.current++;
        setDetail(null);
    };

    const handlePreviewClick = (ad: Ad) => {
        if (!ad.videos?.length) { void handleDetailClick(ad); return; }
        setVideoModal({ open: true, url: ad.videos[0], adId: ad.id, landingUrl: ad.url });
    };

    const handleVideoClose = () => {
        setVideoModal({ open: false, url: '', adId: null, landingUrl: '' });
    };

    return (
        <>
            <div className="app-layout">
            <aside className="app-sidebar" aria-label="工作台侧边栏">
                <div className="brand">
                    <svg className="brand-icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg">
                        <g>
                            <path d="M625.6 516.8l19.2 81.6 104-38.4 4.8 14.4-110.4 40L560 824l-14.4-6.4 83.2-203.2-169.6-25.6 64 217.6c3.2 8-1.6 17.6-11.2 19.2s-17.6-1.6-19.2-11.2l-68.8-232-153.6-22.4 1.6-16 145.6 22.4-28.8-96-116.8 59.2-14.4-28.8 129.6-65.6L480 217.6 254.4 499.2l-12.8-9.6L480 190.4l9.6 6.4 27.2 11.2-96 227.2 177.6 41.6-64-268.8 16-3.2 67.2 278.4 136 32c8 1.6 14.4 11.2 11.2 19.2-1.6 8-11.2 14.4-19.2 11.2l-120-28.8zM608 512l-185.6-43.2 30.4 102.4 176 25.6L608 512z m-46.4-313.6l12.8-9.6L784 484.8l-12.8 9.6-209.6-296zM768 588.8l12.8 9.6-201.6 240-12.8-9.6 201.6-240z m-278.4 240l-11.2 11.2-232-243.2 11.2-11.2 232 243.2z" fill="#06172d"></path>
                            <path d="M400 448m-64 0a64 64 0 1 0 128 0 64 64 0 1 0-128 0Z" fill="#2538b1"></path>
                            <path d="M640 608m-56 0a56 56 0 1 0 112 0 56 56 0 1 0-112 0Z" fill="#2538b1"></path>
                            <path d="M208 624c-44.8 0-80-35.2-80-80s35.2-80 80-80 80 35.2 80 80-35.2 80-80 80z m0-32c27.2 0 48-20.8 48-48s-20.8-48-48-48-48 20.8-48 48 20.8 48 48 48zM528 960c-44.8 0-80-35.2-80-80s35.2-80 80-80 80 35.2 80 80-35.2 80-80 80z m0-32c27.2 0 48-20.8 48-48s-20.8-48-48-48-48 20.8-48 48 20.8 48 48 48zM528 224c-44.8 0-80-35.2-80-80s35.2-80 80-80 80 35.2 80 80-35.2 80-80 80z m0-32c27.2 0 48-20.8 48-48s-20.8-48-48-48-48 20.8-48 48 20.8 48 48 48zM816 624c-44.8 0-80-35.2-80-80s35.2-80 80-80 80 35.2 80 80-35.2 80-80 80z m0-32c27.2 0 48-20.8 48-48s-20.8-48-48-48-48 20.8-48 48 20.8 48 48 48z" fill="#2538b1"></path>
                        </g>
                    </svg>
                    <div className="sidebar-brand-name">Mini Ad Manager <span>Beta</span></div>
                </div>
                <div className="sidebar-account">
                    <button className="sidebar-account-button" aria-label="个人信息管理" aria-expanded={profileOpen} onClick={() => { setProfileOpen(!profileOpen); setCredential(''); setCredentialSaved(false); }}><span className="user-avatar" aria-hidden="true">A</span><span className="sidebar-account-copy"><strong>当前账号</strong><small>{connected ? '运营凭证已设置' : '设置运营凭证'}</small></span><span aria-hidden="true">⌄</span></button>
                    {profileOpen && <div className="profile-settings">
                        <div className="profile-settings-heading"><strong>个人信息管理</strong><button aria-label="关闭个人信息管理" onClick={() => setProfileOpen(false)}>×</button></div>
                        <form onSubmit={e => { e.preventDefault(); setStudioCredential(credential); setConnected(hasStudioCredential()); setCredential(''); setCredentialSaved(true); window.dispatchEvent(new Event('studio-credential-changed')); void loadAds(); }}>
                            <label htmlFor="operator-credential">运营凭证</label>
                            <input id="operator-credential" type="password" autoComplete="off" value={credential} onChange={e => { setCredential(e.target.value); setCredentialSaved(false); }} placeholder={hasStudioCredential() ? '已设置，输入新凭证可更换' : '请输入运营凭证'} />
                            <button type="submit" disabled={!credential.trim()}>保存并连接</button>
                            {credentialSaved && <p role="status">凭证已保存</p>}
                        </form>
                    </div>}
                </div>
                <nav className="sidebar-navigation" aria-label="模块导航">
                    <button className={`sidebar-nav-item ${activeModule === 'list' ? 'active' : ''}`} aria-current={activeModule === 'list' ? 'page' : undefined} onClick={() => navigate('list')}><NavIcon kind="ads" />广告管理</button>
                    <button className={`sidebar-nav-item ${activeModule === 'materials' || creativeActive ? 'group-active' : ''}`} aria-expanded={materialsExpanded} aria-controls="material-navigation" onClick={() => setMaterialsExpanded(previous => !previous)}><NavIcon kind="materials" />素材管理<span className={`sidebar-chevron ${materialsExpanded ? 'expanded' : ''}`} aria-hidden="true">⌄</span></button>
                    <div className="sidebar-subnav" id="material-navigation" hidden={!materialsExpanded}>
                        <button className={`sidebar-nav-item ${activeModule === 'materials' ? 'active' : ''}`} aria-current={activeModule === 'materials' ? 'page' : undefined} onClick={() => navigate('materials')}>已有素材管理</button>
                        <button className={`sidebar-nav-item ${creativeActive ? 'active' : ''}`} aria-current={creativeActive ? 'page' : undefined} onClick={() => navigate('creatives')}>素材生成工作台</button>
                    </div>
                    <button className={`sidebar-nav-item ${activeModule === 'analytics' ? 'active' : ''}`} aria-current={activeModule === 'analytics' ? 'page' : undefined} onClick={() => navigate('analytics')}><NavIcon kind="analytics" />数据分析</button>
                </nav>
                <div className="sidebar-bottom"><span className="sidebar-status-dot" />Mini Ad Manager · Beta</div>
            </aside>

            <main className="app-main">
            <div className="container">
                <header className="management-heading workspace-heading">
                    <div><div className="workspace-breadcrumb">工作台 / {activeModule === 'materials' || creativeActive ? '素材管理 / ' : ''}{moduleInfo[activeModule].title}</div>
                    <h1 className="page-title">{moduleInfo[activeModule].title}</h1>
                    <p>{moduleInfo[activeModule].description}</p></div>
                </header>
                <AdManagement
                    view={activeModule}
                    ads={ads}
                    loading={loadingPage}
                    error={loadError}
                    query={searchQuery}
                    listRevision={listRevision}
                    hasMore={!!nextCursor}
                    onRetry={() => void loadAds()}
                    onClearQuery={clearSearch}
                    onLoadMore={loadNextPage}
                    onEdit={handleEditClick}
                    onCopy={handleCopyClick}
                    onDelete={handleDeleteClick}
                    onDetail={ad => void handleDetailClick(ad)}
                    onPreview={handlePreviewClick}
                    toolbar={<>
                        <button type="button" className="btn btn-primary" onClick={handleCreateClick}><span>+</span> 新建广告</button>
                        <form className="ad-search" role="search" onSubmit={handleSearch}>
                            <div className="ad-search-input">
                                <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></svg>
                                <input type="search" aria-label="搜索广告" placeholder="搜索标题、发布者、正文" maxLength={200} value={searchInput} onChange={event => {
                                    setSearchInput(event.target.value);
                                    if (!event.target.value && queryRef.current) void loadAds('');
                                }} />
                                {(searchInput || searchQuery) && <button className="ad-search-clear" type="button" aria-label="清空搜索" onClick={clearSearch}>×</button>}
                            </div>
                            <button className="btn btn-default" type="submit">搜索</button>
                        </form>
                    </>}
                />
            </div>

            <footer className="workspace-footer">Mini Ad Manager · 广告运营工作台</footer>
            </main>
            </div>

            <AdModal
                isOpen={adModal.open}
                mode={adModal.mode}
                initialData={adModal.data}
                onClose={() => setAdModal({ ...adModal, open: false })}
                onSubmit={handleAdSubmit}
            />

            <DeleteModal
                isOpen={deleteModal.open}
                onClose={() => setDeleteModal({ open: false, id: null })}
                onConfirm={handleConfirmDelete}
            />

            <VideoModal
                isOpen={videoModal.open}
                videoUrl={videoModal.url}
                hint="运营素材预览，不计入广告点击"
                onClose={handleVideoClose}
            />

            {detail && <AdDetailDrawer
                ad={detail.ad} loading={detail.loading} error={detail.error}
                onClose={closeDetail} onRetry={() => void handleDetailClick(detail.ad)}
                onEdit={handleEditClick} onCopy={handleCopyClick} onPreview={handlePreviewClick}
            />}

            <div hidden={creativeActive}><AIAssistantPanel ads={ads} /></div>

            <div className={`toast ${toast.show ? 'show' : ''}`}>
                <span className="toast-icon">✓</span>
                <span>{toast.msg}</span>
            </div>
        </>
    );
}

export default App;
