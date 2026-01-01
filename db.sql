CREATE TABLE activity_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ip_address VARCHAR(45),
    action_type VARCHAR(20), -- 'download', 'convert', 'merge'
    details TEXT,            -- 파일명이나 URL 정보
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);