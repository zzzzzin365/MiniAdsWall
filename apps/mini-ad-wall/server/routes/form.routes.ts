import Router from 'koa-router';
import formController from '../controllers/form.controller';

const router = new Router();

router.get('/api/form-config', formController.getFormConfig);
router.post('/api/form-validate', formController.validateForm);

export default router;
