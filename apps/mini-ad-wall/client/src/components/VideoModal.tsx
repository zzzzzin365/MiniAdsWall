import React, { useRef, useEffect } from 'react';

interface VideoModalProps {
    isOpen: boolean;
    videoUrl: string;
    onEnded: () => void;
    onClose: () => void;
}

const VideoModal: React.FC<VideoModalProps> = ({ isOpen, videoUrl, onEnded, onClose }) => {
    const videoRef = useRef<HTMLVideoElement>(null);

    useEffect(() => {
        if (isOpen && videoRef.current) {
            videoRef.current.play().catch(e => console.error("Autoplay failed", e));
        }
    }, [isOpen]);

    if (!isOpen || !videoUrl) return null;

    return (
        <div className="modal-overlay active">
            <div className="modal-box video-modal-box">
                <div style={{ position: 'relative' }}>
                    <button
                        onClick={onClose}
                        className="video-close-btn"
                    >
                        ✕
                    </button>
                    <video
                        ref={videoRef}
                        src={videoUrl}
                        controls
                        className="video-player"
                        onEnded={onEnded}
                    />
                    <div className="video-hint">
                        视频播放结束后将自动跳转...
                    </div>
                </div>
            </div>
        </div>
    );
};

export default VideoModal;
