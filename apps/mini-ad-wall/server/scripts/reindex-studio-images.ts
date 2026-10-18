import {reindexBrandImages} from '../services/merchant/studio/reindex';
import {closeAdsDatabase} from '../services/ads.database';
async function main() {
  const [brand,environment,principal]=process.argv.slice(2);
  if (!brand || !['synthetic','sandbox','production'].includes(environment) || !principal)
    throw Error('Usage: reindex-studio-images BRAND ENVIRONMENT PRINCIPAL');
  console.log(JSON.stringify(await reindexBrandImages(brand,environment,principal)));
}
main().catch(error=>{console.error(error.message);process.exitCode=1;}).finally(closeAdsDatabase);
