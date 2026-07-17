import Router from 'koa-router';
import aiController from '../controllers/ai.controller';

const router = new Router({
    prefix: '/api/ai'
});

router.post('/creative', aiController.generateCreative);

router.post('/strategy', aiController.generateStrategy);

router.post('/assistant/chat', aiController.chatAssistant);

router.get('/assistant/status', aiController.assistantStatus);

export default router;
