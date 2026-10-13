import { studioSchema } from "../services/merchant/studio/migration";
import { closeAdsDatabase } from "../services/ads.database";
if (require.main === module) {
  const args = process.argv.slice(2);
  const run = async () => {
    if (args.length > 1 || !["up", "status"].includes(args[0] || "status"))
      throw Error("使用 migrate:studio -- up|status");
    console.log(JSON.stringify(await studioSchema(args[0] === "up")));
  };
  run()
    .catch(() => {
      console.error("studio_migration_failed: 请先执行营销013迁移并检查数据库");
      process.exitCode = 1;
    })
    .finally(closeAdsDatabase);
}
