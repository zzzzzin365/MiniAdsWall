import React, { useState, useMemo } from 'react';
import {
    Chart as ChartJS,
    CategoryScale,
    LinearScale,
    BarElement,
    PointElement,
    LineElement,
    Title,
    Tooltip,
    Legend,
    Filler,
    ChartOptions,
} from 'chart.js';
import { Bar, Scatter, Line } from 'react-chartjs-2';
import { Ad } from '../types';
import AnalystImage from '../../Analyst.png';

ChartJS.register(
    CategoryScale,
    LinearScale,
    BarElement,
    PointElement,
    LineElement,
    Title,
    Tooltip,
    Legend,
    Filler
);

interface DataDashboardProps {
    ads: Ad[];
}

const PRICE_RANGES = [
    { min: 0, max: 50, label: '0-50' },
    { min: 50, max: 100, label: '50-100' },
    { min: 100, max: 150, label: '100-150' },
    { min: 150, max: 200, label: '150-200' },
    { min: 200, max: 300, label: '200-300' },
    { min: 300, max: Infinity, label: '300+' },
];

const DataDashboard: React.FC<DataDashboardProps> = ({ ads }) => {
    const [selectedAd, setSelectedAd] = useState<Ad | null>(null);
    const [highlightedRange, setHighlightedRange] = useState<number | null>(null);

    const bidDistributionData = useMemo(() => {
        const counts = PRICE_RANGES.map(range => 
            ads.filter(ad => ad.price >= range.min && ad.price < range.max).length
        );

        return {
            labels: PRICE_RANGES.map(r => r.label),
            datasets: [{
                label: '广告数量',
                data: counts,
                backgroundColor: counts.map((_, index) => 
                    highlightedRange === index 
                        ? '#07E7FF' // 高亮色: 青色
                        : '#054DFF' // 默认色: 鲜蓝
                ),
                borderColor: '#07E7FF',
                borderWidth: 0, // 扁平风格，去掉边框
                borderRadius: 4,
                hoverBackgroundColor: '#07E7FF',
                hoverBorderColor: '#fff',
                hoverBorderWidth: 0,
            }],
        };
    }, [ads, highlightedRange]);

    const bidDistributionOptions: ChartOptions<'bar'> = {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
            mode: 'index',
            intersect: false,
        },
        plugins: {
            legend: {
                display: false,
            },
            title: {
                display: true,
                text: '广告出价分布图',
                color: '#1A202C',
                font: { size: 16, weight: 'bold' },
                padding: { bottom: 20 },
            },
            tooltip: {
                enabled: true,
                backgroundColor: '#fff',
                titleColor: '#054DFF',
                bodyColor: '#4A5568',
                borderColor: '#E2E8F0',
                borderWidth: 1,
                cornerRadius: 8,
                padding: 12,
                displayColors: false,
                callbacks: {
                    title: (items) => `价格区间: ¥${items[0].label}`,
                    label: (item) => {
                        const count = item.raw as number;
                        const percentage = ads.length > 0 ? ((count / ads.length) * 100).toFixed(1) : 0;
                        return [`广告数量: ${count} 个`, `占比: ${percentage}%`];
                    },
                },
            },
        },
        scales: {
            x: {
                grid: { color: 'rgba(0, 0, 0, 0.06)' },
                ticks: { color: '#4A5568' },
                title: {
                    display: true,
                    text: '出价区间 (¥)',
                    color: '#718096',
                },
            },
            y: {
                grid: { color: 'rgba(0, 0, 0, 0.06)' },
                ticks: { 
                    color: '#4A5568',
                    stepSize: 1,
                },
                title: {
                    display: true,
                    text: '广告数量',
                    color: '#718096',
                },
                beginAtZero: true,
            },
        },
        onClick: (_, elements) => {
            if (elements.length > 0) {
                const index = elements[0].index;
                setHighlightedRange(highlightedRange === index ? null : index);
            }
        },
        onHover: (event, elements) => {
            const target = event.native?.target as HTMLElement;
            if (target) {
                target.style.cursor = elements.length > 0 ? 'pointer' : 'default';
            }
        },
    };

    // ========== 图表2: 排名 vs 出价关系图（散点图）数据 ==========
    const rankVsBidData = useMemo(() => {
        return {
            datasets: [{
                label: '广告位置',
                data: ads.map((ad, index) => ({
                    x: ad.price,
                    y: index + 1,
                    ad: ad,
                })),
                backgroundColor: ads.map(ad => 
                    selectedAd?.id === ad.id 
                        ? '#07E7FF'
                        : '#054DFF'
                ),
                borderColor: '#fff',
                borderWidth: 1,
                pointRadius: ads.map(ad => selectedAd?.id === ad.id ? 10 : 6),
                pointHoverRadius: 12,
                hoverBackgroundColor: '#07E7FF',
                hoverBorderColor: '#fff',
                hoverBorderWidth: 2,
            }],
        };
    }, [ads, selectedAd]);

    const rankVsBidOptions: ChartOptions<'scatter'> = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                display: false,
            },
            title: {
                display: true,
                text: '排名 vs 出价关系图',
                color: '#1A202C',
                font: { size: 16, weight: 'bold' },
                padding: { bottom: 20 },
            },
            tooltip: {
                enabled: true,
                backgroundColor: '#fff',
                titleColor: '#054DFF',
                bodyColor: '#4A5568',
                borderColor: '#E2E8F0',
                borderWidth: 1,
                cornerRadius: 8,
                padding: 12,
                callbacks: {
                    title: () => '广告详情',
                    label: (item) => {
                        const data = item.raw as { x: number; y: number; ad: Ad };
                        return [
                            `标题: ${data.ad.title}`,
                            `出价: ¥${data.x}`,
                            `排名: 第 ${data.y} 位`,
                            `点击: ${data.ad.clicks} 次`,
                            '',
                            '💡 点击查看详情'
                        ];
                    },
                },
            },
        },
        scales: {
            x: {
                grid: { color: 'rgba(0, 0, 0, 0.06)' },
                ticks: { color: '#4A5568' },
                title: {
                    display: true,
                    text: '出价 (¥)',
                    color: '#718096',
                },
                beginAtZero: true,
            },
            y: {
                grid: { color: 'rgba(0, 0, 0, 0.06)' },
                ticks: { 
                    color: '#4A5568',
                    stepSize: 1,
                },
                title: {
                    display: true,
                    text: '排名位置',
                    color: '#718096',
                },
                beginAtZero: false,
                min: 0,
                reverse: true,
            },
        },
        onClick: (_, elements) => {
            if (elements.length > 0) {
                const dataIndex = elements[0].index;
                const clickedAd = ads[dataIndex];
                setSelectedAd(selectedAd?.id === clickedAd.id ? null : clickedAd);
            }
        },
        onHover: (event, elements) => {
            const target = event.native?.target as HTMLElement;
            if (target) {
                target.style.cursor = elements.length > 0 ? 'pointer' : 'default';
            }
        },
    };

    const clickTrendData = useMemo(() => {
        const labels = ads.map(ad => 
            ad.title.length > 6 ? ad.title.substring(0, 6) + '...' : ad.title
        );

        return {
            labels,
            datasets: [{
                label: '点击次数',
                data: ads.map(ad => ad.clicks),
                borderColor: '#0070FD',
                backgroundColor: 'rgba(5, 77, 255, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.4, // 平滑曲线
                pointBackgroundColor: ads.map(ad => 
                    selectedAd?.id === ad.id 
                        ? '#07E7FF'
                        : '#054DFF'
                ),
                pointBorderColor: '#fff',
                pointBorderWidth: 1.5,
                pointRadius: ads.map(ad => selectedAd?.id === ad.id ? 8 : 5),
                pointHoverRadius: 10,
                pointHoverBackgroundColor: '#07E7FF',
                pointHoverBorderColor: '#fff',
                pointHoverBorderWidth: 2,
            }],
        };
    }, [ads, selectedAd]);

    const clickTrendOptions: ChartOptions<'line'> = {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
            mode: 'index',
            intersect: false,
        },
        plugins: {
            legend: {
                display: false,
            },
            title: {
                display: true,
                text: '广告点击热度趋势',
                color: '#1A202C',
                font: { size: 16, weight: 'bold' },
                padding: { bottom: 20 },
            },
            tooltip: {
                enabled: true,
                backgroundColor: '#fff',
                titleColor: '#054DFF',
                bodyColor: '#4A5568',
                borderColor: '#E2E8F0',
                borderWidth: 1,
                cornerRadius: 8,
                padding: 12,
                callbacks: {
                    title: (items) => {
                        const index = items[0].dataIndex;
                        return ads[index]?.title || '';
                    },
                    label: (item) => {
                        const index = item.dataIndex;
                        const ad = ads[index];
                        return [
                            `点击次数: ${item.raw} 次`,
                            `出价: ¥${ad?.price || 0}`,
                            `发布者: ${ad?.publisher || ''}`,
                            '',
                            '💡 点击选中此广告'
                        ];
                    },
                },
            },
        },
        scales: {
            x: {
                grid: { color: 'rgba(0, 0, 0, 0.06)' },
                ticks: { 
                    color: '#4A5568',
                    maxRotation: 45,
                    minRotation: 45,
                },
                title: {
                    display: true,
                    text: '广告名称',
                    color: '#718096',
                },
            },
            y: {
                grid: { color: 'rgba(0, 0, 0, 0.06)' },
                ticks: { color: '#4A5568' },
                title: {
                    display: true,
                    text: '点击次数',
                    color: '#718096',
                },
                beginAtZero: true,
            },
        },
        onClick: (_, elements) => {
            if (elements.length > 0) {
                const dataIndex = elements[0].index;
                const clickedAd = ads[dataIndex];
                setSelectedAd(selectedAd?.id === clickedAd.id ? null : clickedAd);
            }
        },
        onHover: (event, elements) => {
            const target = event.native?.target as HTMLElement;
            if (target) {
                target.style.cursor = elements.length > 0 ? 'pointer' : 'default';
            }
        },
    };

    const generateVideoDuration = (videoUrl: string, index: number): number => {
        let hash = 0;
        const str = videoUrl + index;
        for (let i = 0; i < str.length; i++) {
            hash = ((hash << 5) - hash) + str.charCodeAt(i);
            hash |= 0;
        }
        return Math.abs(hash % 56) + 5;
    };

    const videoPerformanceData = useMemo(() => {
        const dataPoints: { x: number; y: number; ad: Ad; videoIndex: number }[] = [];
        
        ads.forEach(ad => {
            if (ad.videos && ad.videos.length > 0) {
                ad.videos.forEach((videoUrl, videoIndex) => {
                    const duration = generateVideoDuration(videoUrl, videoIndex);
                    dataPoints.push({
                        x: duration,
                        y: ad.clicks,
                        ad: ad,
                        videoIndex: videoIndex
                    });
                });
            }
        });

        return {
            datasets: [{
                label: '视频素材',
                data: dataPoints,
                backgroundColor: dataPoints.map(dp => 
                    selectedAd?.id === dp.ad.id 
                        ? '#07E7FF'
                        : '#054DFF'
                ),
                borderColor: '#fff',
                borderWidth: 1.5,
                pointRadius: dataPoints.map(dp => selectedAd?.id === dp.ad.id ? 10 : 7),
                pointHoverRadius: 12,
                hoverBackgroundColor: '#07E7FF',
                hoverBorderColor: '#fff',
                hoverBorderWidth: 2,
            }],
        };
    }, [ads, selectedAd]);

    const videoPerformanceOptions: ChartOptions<'scatter'> = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                display: false,
            },
            title: {
                display: true,
                text: '视频素材表现图',
                color: '#1A202C',
                font: { size: 16, weight: 'bold' },
                padding: { bottom: 20 },
            },
            tooltip: {
                enabled: true,
                backgroundColor: '#fff',
                titleColor: '#054DFF',
                bodyColor: '#4A5568',
                borderColor: '#E2E8F0',
                borderWidth: 1,
                cornerRadius: 8,
                padding: 12,
                callbacks: {
                    title: () => '视频详情',
                    label: (item) => {
                        const data = item.raw as { x: number; y: number; ad: Ad; videoIndex: number };
                        return [
                            `广告: ${data.ad.title}`,
                            `视频时长: ${data.x} 秒`,
                            `点击热度: ${data.y} 次`,
                            `视频序号: #${data.videoIndex + 1}`,
                            '',
                            '💡 点击选中此广告'
                        ];
                    },
                },
            },
        },
        scales: {
            x: {
                grid: { color: 'rgba(0, 0, 0, 0.06)' },
                ticks: { color: '#4A5568' },
                title: {
                    display: true,
                    text: '视频时长 (秒)',
                    color: '#718096',
                },
                beginAtZero: true,
                max: 65,
            },
            y: {
                grid: { color: 'rgba(0, 0, 0, 0.06)' },
                ticks: { color: '#4A5568' },
                title: {
                    display: true,
                    text: '点击热度',
                    color: '#718096',
                },
                beginAtZero: true,
            },
        },
        onClick: (_, elements) => {
            if (elements.length > 0) {
                const dataIndex = elements[0].index;
                const dataPoints = videoPerformanceData.datasets[0].data as { ad: Ad }[];
                const clickedAd = dataPoints[dataIndex]?.ad;
                if (clickedAd) {
                    setSelectedAd(selectedAd?.id === clickedAd.id ? null : clickedAd);
                }
            }
        },
        onHover: (event, elements) => {
            const target = event.native?.target as HTMLElement;
            if (target) {
                target.style.cursor = elements.length > 0 ? 'pointer' : 'default';
            }
        },
    };

    // 计算统计数据
    const stats = useMemo(() => {
        if (ads.length === 0) return { totalAds: 0, avgPrice: 0, totalClicks: 0, maxPrice: 0 };
        const totalClicks = ads.reduce((sum, ad) => sum + ad.clicks, 0);
        const avgPrice = ads.reduce((sum, ad) => sum + ad.price, 0) / ads.length;
        const maxPrice = Math.max(...ads.map(ad => ad.price));
        return { totalAds: ads.length, avgPrice, totalClicks, maxPrice };
    }, [ads]);

    if (ads.length === 0) {
        return (
            <div className="data-dashboard">
                <div className="dashboard-empty">
                    <p>暂无广告数据，请先创建广告</p>
                </div>
            </div>
        );
    }

    return (
        <div className="data-dashboard">

            {/* 统计概览 */}
            <div className="dashboard-stats">
                <div className="dashboard-stat-item">
                    <div className="stat-value">{stats.totalAds}</div>
                    <div className="stat-label">广告总数</div>
                </div>
                <div className="dashboard-stat-item">
                    <div className="stat-value">¥{stats.avgPrice.toFixed(0)}</div>
                    <div className="stat-label">平均出价</div>
                </div>
                <div className="dashboard-stat-item">
                    <div className="stat-value">{stats.totalClicks}</div>
                    <div className="stat-label">总点击数</div>
                </div>
                <div className="dashboard-stat-item">
                    <div className="stat-value">¥{stats.maxPrice}</div>
                    <div className="stat-label">最高出价</div>
                </div>
            </div>

            {selectedAd && (
                <div className="selected-ad-card">
                    <div className="selected-ad-title">{selectedAd.title}</div>
                    <span className="selected-ad-badge">已选中</span>
                    
                    <div className="selected-ad-info">
                        <span>出价: ¥{selectedAd.price}</span>
                        <span>点击: {selectedAd.clicks} 次</span>
                        <span>发布者: {selectedAd.publisher}</span>
                    </div>
                    
                    <button className="selected-ad-close" onClick={() => setSelectedAd(null)}>×</button>
                </div>
            )}

            <div className="charts-grid">
                <div className="chart-container">
                    <div className="chart-wrapper">
                        <Bar data={bidDistributionData} options={bidDistributionOptions} />
                    </div>
                    <div className="chart-hint">💡 点击柱状图高亮价格区间</div>
                </div>

                <div className="chart-container">
                    <div className="chart-wrapper">
                        <Scatter data={rankVsBidData} options={rankVsBidOptions} />
                    </div>
                    <div className="chart-hint">💡 点击数据点查看广告详情</div>
                </div>

                <div className="chart-container">
                    <div className="chart-wrapper">
                        <Scatter data={videoPerformanceData} options={videoPerformanceOptions} />
                    </div>
                    <div className="chart-hint">💡 横轴: 视频时长 | 纵轴: 点击热度</div>
                </div>

                <div className="chart-container add-chart-card">
                    <div className="add-chart-content">
                        <img src={AnalystImage} alt="分析师" className="add-chart-image" />
                        <span className="add-chart-text">新增更多图表</span>
                    </div>
                </div>

                <div className="chart-container chart-full-width">
                    <div className="chart-wrapper chart-wrapper-wide">
                        <Line data={clickTrendData} options={clickTrendOptions} />
                    </div>
                    <div className="chart-hint">💡 悬停查看详情，点击选中广告</div>
                </div>
            </div>
        </div>
    );
};

export default DataDashboard;
