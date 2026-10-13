/**
 * The Compose command lines, and the Docker-absent branch.
 *
 * ## Why the command lines are pinned at all
 *
 * `web/e2e/stack.ts` documents at length that the e2e stack is
 * `postgres migrate api worker` on the repository's own `docker-compose.yml`,
 * and that the compose `web` service is excluded for a specific reason. The
 * desktop shell makes the same choice for the same reasons, and a shell that
 * quietly grew a fifth service — or that lost `--wait` — would fail in ways that
 * look like Docker's: a readiness poll against an API whose migrations have not
 * run yet, or a worker that is `Up` but never polling.
 *
 * Two of these assertions are about a *difference* from the e2e harness, and
 * both differences are load-bearing:
 *
 *   * `up` has no `--build`. The e2e builds because it tests the built images; a
 *     desktop launch should reuse the images it has, on a schedule an operator
 *     can predict.
 *   * `down` has no `-v`. `compose down -v` removes the named volumes, which is
 *     correct for an ephemeral CI run and would delete the operator's runs,
 *     approvals and refund ledger every time they quit the app.
 *
 * The Docker-absent cases are the D1 acceptance in three shapes: a machine with
 * no `docker` on PATH, a machine whose Docker Desktop has not started, and a
 * machine with a daemon but no Compose plugin. Each must name what is missing
 * and what to install, and none may start anything or retry — a start-up path
 * that keeps trying against a missing prerequisite is the spinner that never
 * resolves, which is the exact outcome the acceptance rules out.
 */

import { expect, test } from "@playwright/test";

import { assessDocker, COMPOSE_SERVICES, composeDownArgs, composeUpArgs } from "../src/lifecycle/compose";
import { fakeDeps, fakeRunner, outcome } from "./fakes";

test.describe("compose command construction", () => {
  test("starts postgres, migrate, api and worker — and not the compose web service", () => {
    expect([...COMPOSE_SERVICES]).toEqual(["postgres", "migrate", "api", "worker"]);
    const args = composeUpArgs();
    expect(args.slice(-4)).toEqual(["postgres", "migrate", "api", "worker"]);
    expect(args).not.toContain("web");
  });

  test("`up` waits, with the same deadline shape the e2e harness uses", () => {
    const args = composeUpArgs();
    expect(args.slice(0, 2)).toEqual(["compose", "up"]);
    expect(args).toContain("-d");
    expect(args).toContain("--wait");
    expect(args[args.indexOf("--wait-timeout") + 1]).toBe("300");
  });

  test("`up` does not rebuild: a desktop launch reuses the images it has", () => {
    expect(composeUpArgs()).not.toContain("--build");
  });

  test("`down` removes containers but keeps the volumes", () => {
    const args = composeDownArgs();
    expect(args.slice(0, 2)).toEqual(["compose", "down"]);
    expect(args).toContain("--remove-orphans");
    // The one assertion here that is about data, not about flags: `compose down
    // -v` deletes `postgres-data`, so quitting the app would delete the
    // operator's data. `removeVolumes` exists so the destructive form is a
    // deliberate call and never the default.
    expect(args).not.toContain("-v");
    expect(composeDownArgs({ removeVolumes: true })).toContain("-v");
  });
});

test.describe("Docker absent", () => {
  test("a missing docker executable names Docker and says what to install", async () => {
    const runner = fakeRunner({
      answers: { "docker info": outcome({ ok: false, status: null, spawnError: enoentError("docker") }) },
    });
    const deps = fakeDeps({ runner });

    const problem = await assessDocker(deps);

    expect(problem?.kind).toBe("docker-not-installed");
    expect(problem?.title).toBe("Docker is not installed");
    expect(problem?.remedy).toContain("Docker Desktop");
    expect(problem?.remedy).toContain("https://www.docker.com/products/docker-desktop/");
    // Nothing was started, and nothing was retried.
    expect(runner.commands).toHaveLength(1);
    expect(runner.commands[0]?.args).toEqual(["info"]);
  });

  test("an installed Docker that is not running says to start it, and quotes the daemon", async () => {
    const runner = fakeRunner({
      answers: {
        "docker info": outcome({
          ok: false,
          status: 1,
          stderr: "error during connect: cannot connect to the Docker daemon at npipe:////./pipe/docker_engine",
        }),
      },
    });

    const problem = await assessDocker(fakeDeps({ runner }));

    expect(problem?.kind).toBe("docker-not-running");
    expect(problem?.remedy).toContain("Start Docker Desktop");
    // The daemon's own words are the diagnosis, so they are quoted rather than
    // summarised into something the operator cannot match against their machine.
    expect(problem?.detail).toContain("cannot connect to the Docker daemon");
  });

  test("a running Docker with no Compose plugin says to install the plugin", async () => {
    const runner = fakeRunner({
      answers: { "docker compose version": outcome({ ok: false, status: 1, stderr: "is not a docker command" }) },
    });

    const problem = await assessDocker(fakeDeps({ runner }));

    expect(problem?.kind).toBe("compose-plugin-missing");
    expect(problem?.remedy).toContain("docker compose install");
  });

  test("a usable Docker produces no problem, after exactly the two probes", async () => {
    const runner = fakeRunner();

    expect(await assessDocker(fakeDeps({ runner }))).toBeNull();
    expect(runner.commands.map((entry) => `${entry.command} ${entry.args.join(" ")}`)).toEqual([
      "docker info",
      "docker compose version",
    ]);
  });
});

function enoentError(command: string): NodeJS.ErrnoException {
  return Object.assign(new Error(`spawn ${command} ENOENT`), { code: "ENOENT" });
}