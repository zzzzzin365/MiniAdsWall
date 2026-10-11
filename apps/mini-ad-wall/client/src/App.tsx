import React, { useState, useEffect, useRef } from 'react';
import { getAdsPage, getAd, createAd, updateAd, deleteAd } from './api';
import AdManagement from './components/AdManagement';
import AdDetailDrawer from './components/AdDetailDrawer';
import AdModal from './components/AdModal';
import DeleteModal from './components/DeleteModal';
import VideoModal from './components/VideoModal';
import AIAssistantPanel from './components/AIAssistantPanel';
import { Ad, AdInput, AdModalState, DeleteModalState, VideoModalState, ToastState } from './types';

function App() {
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
            <div className="navbar">
                <div className="brand">
                    <svg className="brand-icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg">
                        <g>
                            <path d="M625.6 516.8l19.2 81.6 104-38.4 4.8 14.4-110.4 40L560 824l-14.4-6.4 83.2-203.2-169.6-25.6 64 217.6c3.2 8-1.6 17.6-11.2 19.2s-17.6-1.6-19.2-11.2l-68.8-232-153.6-22.4 1.6-16 145.6 22.4-28.8-96-116.8 59.2-14.4-28.8 129.6-65.6L480 217.6 254.4 499.2l-12.8-9.6L480 190.4l9.6 6.4 27.2 11.2-96 227.2 177.6 41.6-64-268.8 16-3.2 67.2 278.4 136 32c8 1.6 14.4 11.2 11.2 19.2-1.6 8-11.2 14.4-19.2 11.2l-120-28.8zM608 512l-185.6-43.2 30.4 102.4 176 25.6L608 512z m-46.4-313.6l12.8-9.6L784 484.8l-12.8 9.6-209.6-296zM768 588.8l12.8 9.6-201.6 240-12.8-9.6 201.6-240z m-278.4 240l-11.2 11.2-232-243.2 11.2-11.2 232 243.2z" fill="#06172d"></path>
                            <path d="M400 448m-64 0a64 64 0 1 0 128 0 64 64 0 1 0-128 0Z" fill="#2538b1"></path>
                            <path d="M640 608m-56 0a56 56 0 1 0 112 0 56 56 0 1 0-112 0Z" fill="#2538b1"></path>
                            <path d="M208 624c-44.8 0-80-35.2-80-80s35.2-80 80-80 80 35.2 80 80-35.2 80-80 80z m0-32c27.2 0 48-20.8 48-48s-20.8-48-48-48-48 20.8-48 48 20.8 48 48 48zM528 960c-44.8 0-80-35.2-80-80s35.2-80 80-80 80 35.2 80 80-35.2 80-80 80z m0-32c27.2 0 48-20.8 48-48s-20.8-48-48-48-48 20.8-48 48 20.8 48 48 48zM528 224c-44.8 0-80-35.2-80-80s35.2-80 80-80 80 35.2 80 80-35.2 80-80 80z m0-32c27.2 0 48-20.8 48-48s-20.8-48-48-48-48 20.8-48 48 20.8 48 48 48zM816 624c-44.8 0-80-35.2-80-80s35.2-80 80-80 80 35.2 80 80-35.2 80-80 80z m0-32c27.2 0 48-20.8 48-48s-20.8-48-48-48-48 20.8-48 48 20.8 48 48 48z" fill="#2538b1"></path>
                        </g>
                    </svg>
                    Mini Ad Manager <span>Beta</span>
                </div>
                <div>
                    <div className="user-avatar">A</div>
                </div>
            </div>

            <div className="container">
                <div className="management-heading">
                    <div className="page-title">广告管理</div>
                    <span>管理广告与创意素材，查看出价和点击数据</span>
                </div>
                <AdManagement
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

            <footer className="footer">
                <div className="footer-content">
                    <div className="footer-section">
                        <h4>产品</h4>
                        <ul className="footer-links">
                            <li><a href="#">广告管理</a></li>
                            <li><a href="#data-analysis">数据分析</a></li>
                            <li><a href="#">智能投放</a></li>
                            <li><a href="#">效果优化</a></li>
                        </ul>
                    </div>
                    <div className="footer-section">
                        <h4>解决方案</h4>
                        <ul className="footer-links">
                            <li><a href="#">企业营销</a></li>
                            <li><a href="#">品牌推广</a></li>
                            <li><a href="#">效果广告</a></li>
                            <li><a href="#">内容营销</a></li>
                        </ul>
                    </div>
                    <div className="footer-section">
                        <h4>支持</h4>
                        <ul className="footer-links">
                            <li><a href="#">帮助中心</a></li>
                            <li><a href="#">API文档</a></li>
                            <li><a href="#">开发者指南</a></li>
                            <li><a href="#">联系我们</a></li>
                        </ul>
                    </div>
                    <div className="footer-section">
                        <h4>关于</h4>
                        <ul className="footer-links">
                            <li><a href="#">公司介绍</a></li>
                            <li><a href="#">隐私政策</a></li>
                            <li><a href="#">服务条款</a></li>
                            <li><a href="#">加入我们</a></li>
                        </ul>
                    </div>
                </div>
                <div className="footer-bottom">
                    <div>
                        © 2025 Mini Ad Manager. All rights reserved.
                    </div>
                    <div className="footer-social">
                        <a href="#" title="微信">W</a>
                        <a href="#" title="微博">W</a>
                        <a href="#" title="GitHub">G</a>
                        <a href="#" title="LinkedIn">L</a>
                    </div>
                </div>
            </footer>

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

            <AIAssistantPanel ads={ads} />

            <div className={`toast ${toast.show ? 'show' : ''}`}>
                <span className="toast-icon">✓</span>
                <span>{toast.msg}</span>
            </div>
        </>
    );
}

export default App;
