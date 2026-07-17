import React from 'react';

interface DeleteModalProps {
    isOpen: boolean;
    onClose: () => void;
    onConfirm: () => void;
}

const DeleteModal: React.FC<DeleteModalProps> = ({ isOpen, onClose, onConfirm }) => {
    if (!isOpen) return null;

    return (
        <div className="modal-overlay active">
            <div className="modal-box" style={{ width: '360px' }}>
                <div className="modal-header">确认删除</div>
                <div className="modal-body">
                    <p style={{ color: '#595959', margin: 0 }}>删除后无法恢复，确定要删除这条广告吗？</p>
                </div>
                <div className="modal-footer">
                    <button className="btn btn-default" onClick={onClose}>暂不删除</button>
                    <button className="btn btn-danger" onClick={onConfirm}>确认删除</button>
                </div>
            </div>
        </div>
    );
};

export default DeleteModal;
