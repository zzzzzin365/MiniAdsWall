import Koa from 'koa';
import { koaBody } from 'koa-body';
import cors from '@koa/cors';
import serve from 'koa-static';
import config from './config';
import { registerRoutes } from './routes';
import errorHandler from './middlewares/errorHandler';
import responseTime from './middlewares/responseTime';
import requestLogger from './middlewares/requestLogger';

const app = new Koa();

app.use(errorHandler);
app.use(responseTime);
app.use(cors());
app.use(koaBody());
app.use(requestLogger);
app.use(serve(config.UPLOAD_DIR));
registerRoutes(app);

export default app;
