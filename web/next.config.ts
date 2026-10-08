import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `standalone` makes `next build` trace exactly the files the server needs
  // and emit them, with a minimal `server.js`, into `.next/standalone`. That is
  // what the multi-stage web/Dockerfile copies into the runtime image, so the
  // production image carries no dev dependencies and no full `node_modules`.
  //
  // It is set here rather than passed to the build because `next build` reads
  // it from this file, and because it is a property of the app's deployment
  // shape, not of one Docker invocation.
  output: "standalone",
};

export default nextConfig;
