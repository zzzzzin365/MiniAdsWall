import { adFormConfig } from '../config/formConfig';
import { FormFieldConfig, ValidationResult, ValidationError } from '../types';

function getAdFormConfig(): FormFieldConfig[] {
    return adFormConfig;
}

function validateByConfig(data: Record<string, any>, config: FormFieldConfig[] = adFormConfig): ValidationResult {
    const errors: ValidationError[] = [];

    for (const field of config) {
        const value = data[field.field];

        if (field.required) {
            if (value === undefined || value === null || value === '') {
                errors.push({
                    field: field.field,
                    message: `${field.name}不能为空`
                });
                continue;
            }
        }

        if (value !== undefined && value !== null && value !== '' && field.validator) {
            const validator = field.validator;

            if (validator.maxLength && String(value).length > validator.maxLength) {
                errors.push({
                    field: field.field,
                    message: validator.message || `${field.name}超过最大长度`
                });
            }

            if (validator.pattern) {
                const regex = new RegExp(validator.pattern);
                if (!regex.test(value)) {
                    errors.push({
                        field: field.field,
                        message: validator.message || `${field.name}格式不正确`
                    });
                }
            }

            if (field.component === 'Number') {
                const numValue = parseFloat(value);
                if (isNaN(numValue)) {
                    errors.push({
                        field: field.field,
                        message: `${field.name}必须是数字`
                    });
                } else {
                    if (validator.min !== undefined && numValue < validator.min) {
                        errors.push({
                            field: field.field,
                            message: validator.message || `${field.name}不能小于${validator.min}`
                        });
                    }
                    if (validator.max !== undefined && numValue > validator.max) {
                        errors.push({
                            field: field.field,
                            message: validator.message || `${field.name}不能大于${validator.max}`
                        });
                    }
                }
            }

            if (Array.isArray(value) && validator.maxCount) {
                if (value.length > validator.maxCount) {
                    errors.push({
                        field: field.field,
                        message: validator.message || `${field.name}数量超过限制`
                    });
                }
            }
        }
    }

    return {
        valid: errors.length === 0,
        errors
    };
}

export default {
    getAdFormConfig,
    validateByConfig
};
