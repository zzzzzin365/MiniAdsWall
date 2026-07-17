import React from 'react';
import AdCard from './AdCard';
import { Ad } from '../types';

interface VirtualAdGridProps {
    ads: Ad[];
    onEdit: (ad: Ad) => void;
    onCopy: (ad: Ad) => void;
    onDelete: (ad: Ad) => void;
    onClick: (ad: Ad) => void;
}

/**
 * 广告网格组件
 * 当前使用普通 CSS Grid 渲染，适合中小规模数据
 * 如需处理大量数据（1000+），可引入 react-virtuoso 等虚拟化库
 */
const VirtualAdGrid: React.FC<VirtualAdGridProps> = ({ 
    ads, 
    onEdit, 
    onCopy, 
    onDelete, 
    onClick 
}) => {
    return (
        <div className="ad-grid">
            {ads.map(ad => (
                <AdCard
                    key={ad.id}
                    ad={ad}
                    onEdit={onEdit}
                    onCopy={onCopy}
                    onDelete={onDelete}
                    onClick={onClick}
                />
            ))}
        </div>
    );
};

export default VirtualAdGrid;
