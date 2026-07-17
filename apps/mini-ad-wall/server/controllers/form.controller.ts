import { RouterContext } from 'koa-router';
import formService from '../services/form.service';

async function getFormConfig(ctx: RouterContext): Promise<void> {
    const config = formService.getAdFormConfig();
    ctx.body = config;
}

async function validateForm(ctx: RouterContext): Promise<void> {
    const data = ctx.request.body as Record<string, any>;
    const result = formService.validateByConfig(data);

    if (!result.valid) {
        ctx.status = 400;
        ctx.body = {
            valid: false,
            errors: result.errors
        };
        return;
    }

    ctx.body = {
        valid: true,
        errors: []
    };
}

export default {
    getFormConfig,
    validateForm
};
