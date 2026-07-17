import app from './app';
import config from './config';

app.listen(config.PORT, () => {
    console.log(`Server running on http://localhost:${config.PORT}`);
    console.log(`Environment: ${process.env.NODE_ENV || 'development'}`);
});
