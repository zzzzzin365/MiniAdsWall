import path from 'path';
import { Config } from '../types';

const config: Config = {
    PORT: process.env.PORT || 3001,
    HOST: process.env.HOST || '0.0.0.0',
    DATA_FILE: path.join(__dirname, '..', 'data.json'),
    UPLOAD_DIR: path.join(__dirname, '..', 'uploads'),
    AD_SCORE_FACTOR: 0.42,
    
    OPENROUTER_API_KEY: process.env.OPENROUTER_API_KEY || '',
    OPENROUTER_MODEL: 'mistralai/devstral-2512:free',
    OPENROUTER_API_URL: process.env.OPENROUTER_API_URL || 'https://openrouter.ai/api/v1/chat/completions',
    ADS_AGENT_API_URL: process.env.ADS_AGENT_API_URL || 'http://localhost:8000',
    
    DEFAULT_ADS: [
        {
            id: '1',
            title: '巨量引擎1',
            publisher: '字节广告君',
            content: '巨量引擎是字节跳动旗下综合的数字化营销服务平台，致力于让不分体量、地域的企业及个体，都能通过数字化技术激发创造、驱动生意，实现商业的可持续增长。',
            url: 'https://www.oceanengine.com/',
            price: 5.0,
            clicks: 1,
            videos: []
        },
        {
            id: '2',
            title: '巨量引擎2',
            publisher: '字节广告君',
            content: '巨量引擎是字节跳动旗下综合的数字化营销服务平台，致力于让不分体量、地域的企业及个体，都能通过数字化技术激发创造、驱动生意，实现商业的可持续增长。',
            url: 'https://www.oceanengine.com/',
            price: 5.0,
            clicks: 1,
            videos: []
        },
        {
            id: '3',
            title: '巨量引擎3',
            publisher: '字节广告君',
            content: '巨量引擎是字节跳动旗下综合的数字化营销服务平台，致力于让不分体量、地域的企业及个体，都能通过数字化技术激发创造、驱动生意，实现商业的可持续增长。',
            url: 'https://www.oceanengine.com/',
            price: 5.0,
            clicks: 1,
            videos: []
        }
    ]
};

export default config;
