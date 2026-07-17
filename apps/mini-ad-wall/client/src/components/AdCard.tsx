import React, { useState, useRef, useEffect } from 'react';
import { Ad } from '../types';

interface AdCardProps {
    ad: Ad;
    onEdit: (ad: Ad) => void;
    onCopy: (ad: Ad) => void;
    onDelete: (ad: Ad) => void;
    onClick: (ad: Ad) => void;
}

const AdCard: React.FC<AdCardProps> = ({ ad, onEdit, onCopy, onDelete, onClick }) => {
    const [menuOpen, setMenuOpen] = useState(false);
    const menuRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
                setMenuOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, []);

    const handleMenuClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        setMenuOpen(!menuOpen);
    };

    const handleAction = (e: React.MouseEvent, action: (ad: Ad) => void) => {
        e.stopPropagation();
        setMenuOpen(false);
        action(ad);
    };

    return (
        <div className="ad-card">
            <div className="card-body" onClick={() => onClick(ad)}>
                <div className="card-header">
                    <h3 className="ad-title" title={ad.title}>{ad.title}</h3>
                </div>
                <div className="ad-publisher">{ad.publisher}</div>
                <div className="ad-content-preview">{ad.content}</div>
            </div>
            <div className="card-footer">
                <div className="data-item">
                    <span>热度</span>
                    <span className="data-value">{ad.clicks || 0}</span>
                </div>
                <div className="data-item" style={{ alignItems: 'flex-end' }}>
                    <span>出价</span>
                    <span className="data-value price">¥{ad.price}</span>
                </div>
            </div>
            <div className="card-actions">
                <div className="more-btn" onClick={handleMenuClick}>•••</div>
                <div ref={menuRef} className={`op-menu ${menuOpen ? 'show' : ''}`}>
                    <div className="op-item" onClick={(e) => handleAction(e, onEdit)}>编辑</div>
                    <div className="op-item" onClick={(e) => handleAction(e, onCopy)}>复制</div>
                    <div className="op-item danger" onClick={(e) => handleAction(e, onDelete)}>删除</div>
                </div>
            </div>
        </div>
    );
};

export default AdCard;
