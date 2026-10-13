import { tick } from "../services/merchant/studio/worker";
import { studioSchema } from "../services/merchant/studio/migration";
import { closeAdsDatabase } from "../services/ads.database";
async function main() {
  const args = process.argv.slice(2);
  if (
    args.length > 1 ||
    (args.length && !["--once", "--drain"].includes(args[0]))
  )
    throw Error("使用studio:worker -- --once|--drain");
  await studioSchema();
  let stop = false;
  process.once("SIGTERM", () => {
    stop = true;
  });
  process.once("SIGINT", () => {
    stop = true;
  });
  do {
    const worked = await tick();
    if (args[0] === "--once" || (args[0] === "--drain" && !worked)) break;
    if (!worked) await new Promise((r) => setTimeout(r, 1000));
  } while (!stop);
}
if (require.main === module)
  main()
    .catch(() => {
      console.error("studio_worker_failed");
      process.exitCode = 1;
    })
    .finally(closeAdsDatabase);
