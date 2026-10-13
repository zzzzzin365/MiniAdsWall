const app=require('../../dist/app').default;
const {closeAdsDatabase}=require('../../dist/services/ads.database');
const server=app.listen(0,'127.0.0.1',()=>process.send({port:server.address().port}));
process.once('SIGTERM',()=>server.close(async()=>{await closeAdsDatabase();process.exit(0);}));
