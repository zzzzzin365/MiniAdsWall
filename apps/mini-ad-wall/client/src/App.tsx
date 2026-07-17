import React, { useState, useEffect } from 'react';
import { getAds, createAd, updateAd, deleteAd, clickAd } from './api';
import VirtualAdGrid from './components/VirtualAdGrid';
import AdModal from './components/AdModal';
import DeleteModal from './components/DeleteModal';
import VideoModal from './components/VideoModal';
import DataDashboard from './components/DataDashboard';
import AIAssistantPanel from './components/AIAssistantPanel';
import { Ad, AdInput, AdModalState, DeleteModalState, VideoModalState, ToastState } from './types';

function App() {
    const [ads, setAds] = useState<Ad[]>([]);
    const [adModal, setAdModal] = useState<AdModalState>({ open: false, mode: 'create', data: null });
    const [deleteModal, setDeleteModal] = useState<DeleteModalState>({ open: false, id: null });
    const [videoModal, setVideoModal] = useState<VideoModalState>({ open: false, url: '', adId: null, landingUrl: '' });
    const [toast, setToast] = useState<ToastState>({ show: false, msg: '' });

    useEffect(() => {
        loadAds();
    }, []);

    const loadAds = async () => {
        try {
            const data = await getAds();
            setAds(data);
        } catch (error) {
            console.error("Failed to load ads", error);
        }
    };

    const showToast = (msg: string) => {
        setToast({ show: true, msg });
        setTimeout(() => setToast({ show: false, msg: '' }), 3000);
    };

    const handleCreateClick = () => {
        setAdModal({ open: true, mode: 'create', data: null });
    };

    const handleEditClick = (ad: Ad) => {
        setAdModal({ open: true, mode: 'edit', data: ad });
    };

    const handleCopyClick = (ad: Ad) => {
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
                showToast(`已复制为草稿：${formData.title}`);
            } else if (adModal.mode === 'edit' && adModal.data) {
                await updateAd(adModal.data.id, formData);
                showToast('广告更新成功');
            }
            setAdModal({ ...adModal, open: false });
            loadAds();
        } catch (error) {
            console.error("Operation failed", error);
            alert("操作失败，请重试");
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
            alert("删除失败，请重试");
        }
    };

    const handleAdCardClick = async (ad: Ad) => {
        const hasVideos = ad.videos && ad.videos.length > 0;

        if (hasVideos) {
            const randomIndex = Math.floor(Math.random() * ad.videos.length);
            const videoUrl = ad.videos[randomIndex];
            setVideoModal({
                open: true,
                url: videoUrl,
                adId: ad.id,
                landingUrl: ad.url
            });
        } else {
            setVideoModal({
                open: true,
                url: 'http://localhost:3001/test1.mov',
                adId: ad.id,
                landingUrl: ad.url
            });
        }

        try {
            await clickAd(ad.id);
            loadAds();
        } catch (error) {
            console.error("Click tracking failed", error);
        }
    };

    const openLandingPage = (url: string) => {
        window.open(url.startsWith('http') ? url : `http://${url}`, '_blank');
    };

    const handleVideoEnded = () => {
        const { landingUrl } = videoModal;
        setVideoModal({ open: false, url: '', adId: null, landingUrl: '' });
        openLandingPage(landingUrl);
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
                <div className="toolbar">
                    <div className="page-title">广告管理</div>
                    <button className="btn btn-primary" onClick={handleCreateClick}>
                        <span>+</span> 新建广告
                    </button>
                </div>

                {ads.length === 0 ? (
                    <div className="ad-grid">
                        <div className="empty-state">
                            <div className="empty-state-icon">📭</div>
                            <div className="empty-state-text">暂无广告数据，请点击右上角新建</div>
                        </div>
                    </div>
                ) : (
                    <VirtualAdGrid
                        ads={ads}
                        onEdit={handleEditClick}
                        onCopy={handleCopyClick}
                        onDelete={handleDeleteClick}
                        onClick={handleAdCardClick}
                    />
                )}

                <div className="ranking-alert">
                    <strong>算法说明</strong>
                    当前排名基于：Pricing + (Pricing × Clicks × 0.42)
                </div>

                <DataDashboard ads={ads} />
            </div>

            <footer className="footer">
                <div className="footer-content">
                    <div className="footer-section">
                        <h4>产品</h4>
                        <ul className="footer-links">
                            <li><a href="#">广告管理</a></li>
                            <li><a href="#">数据分析</a></li>
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
                onEnded={handleVideoEnded}
                onClose={handleVideoClose}
            />

            <AIAssistantPanel ads={ads} />

            <div className={`toast ${toast.show ? 'show' : ''}`}>
                <span className="toast-icon">✓</span>
                <span>{toast.msg}</span>
            </div>
        </>
    );
}

export default App;
