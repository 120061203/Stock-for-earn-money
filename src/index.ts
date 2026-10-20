import { Container, getContainer } from "@cloudflare/containers";

interface Env {
  STOCK_SCREEN_CONTAINER: DurableObjectNamespace<StockScreenContainer>;
  FUGLE_API_KEY?: string;
  FINMIND_TOKEN?: string;
  MARKET_DATA_PROVIDER?: string;
  ALLOWED_HOSTS?: string;
  ALLOWED_ORIGINS?: string;
}

export class StockScreenContainer extends Container<Env> {
  // app.py 預設監聽的埠，對應 Dockerfile 的 EXPOSE/PORT。
  defaultPort = 8765;
  // 閒置多久沒有請求就把 container 睡眠，省資源；掃描本身不會超過這個時間。
  sleepAfter = "10m";

  envVars = {
    APP_HOST: "0.0.0.0",
    PORT: "8765",
    FUGLE_API_KEY: this.env.FUGLE_API_KEY ?? "",
    FINMIND_TOKEN: this.env.FINMIND_TOKEN ?? "",
    MARKET_DATA_PROVIDER: this.env.MARKET_DATA_PROVIDER ?? "fugle",
    ALLOWED_HOSTS: this.env.ALLOWED_HOSTS ?? "",
    ALLOWED_ORIGINS: this.env.ALLOWED_ORIGINS ?? "",
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // 這個 App 的掃描狀態（ScanJob）與資料快照都是單一 process 內的本機狀態，
    // 所以固定路由到同一個 container instance，不要按 request 分散到多個實例。
    const container = getContainer(env.STOCK_SCREEN_CONTAINER);
    return container.fetch(request);
  },
};
