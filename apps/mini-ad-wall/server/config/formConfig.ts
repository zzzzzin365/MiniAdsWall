import { FormFieldConfig } from '../types';

export const adFormConfig: FormFieldConfig[] = [
    {
        field: 'title',
        name: '广告标题',
        component: 'Input',
        placeholder: '请输入广告标题',
        required: true,
        validator: {
            maxLength: 50,
            message: '标题最多50个字符'
        }
    },
    {
        field: 'publisher',
        name: '发布人',
        component: 'Input',
        placeholder: '例如：市场部-张三',
        required: true,
        validator: {
            maxLength: 20,
            message: '发布人最多20个字符'
        }
    },
    {
        field: 'content',
        name: '内容文案',
        component: 'Textarea',
        placeholder: '请输入简短的广告描述...',
        required: true,
        validator: {
            maxLength: 200,
            message: '内容最多200个字符'
        }
    },
    {
        field: 'url',
        name: '落地页链接',
        component: 'Input',
        placeholder: 'https://...',
        required: true,
        validator: {
            pattern: '^https?:\\/\\/.+',
            message: '请输入有效的URL地址'
        }
    },
    {
        field: 'price',
        name: '竞价出价 (RMB)',
        component: 'Number',
        placeholder: '0.00',
        required: true,
        validator: {
            min: 0,
            max: 99999,
            message: '出价必须在0-99999之间'
        }
    },
    {
        field: 'videos',
        name: '上传视频',
        component: 'VideoUpload',
        required: false,
        validator: {
            maxCount: 5,
            message: '最多上传5个视频'
        }
    }
];
