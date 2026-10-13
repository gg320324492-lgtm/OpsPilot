/**
 * The bootstrap page's behaviour.
 *
 * Plain ES modules-free browser JavaScript on purpose: the page is served from
 * a custom protocol with a `script-src 'self'` CSP and no bundler is involved,
 * and the whole renderer is four hundred lines of DOM writing. What it does is
 * turn the main process's progress events into three things an operator can use
 * — a headline saying what is happening, the log Compose actually printed, and,
 * when something goes wrong, a failure block naming what to install.
 *
 * ## Why the ownership line is on screen
 *
 * "Using the backend already running on …" and "Backend ready on …" are the two
 * answers to a question this app is judged on and an operator cannot otherwise
 * answer: *did I start that stack, and is it going to stop when I quit?* The
 * event carries `ownership`, and the page prints it rather than leaving the
 * difference implicit in a log line nobody reads.
 */

(function bootstrap() {
  "use strict";

  const headline = document.getElementById("headline");
  const detail = document.getElementById("detail");
  const failure = document.getElementById("failure");
  const failureTitle = document.getElementById("failure-title");
  const failureDetail = document.getElementById("failure-detail");
  const failureRemedy = document.getElementById("failure-remedy");
  const log = document.getElementById("log");
  const ownership = document.getElementById("ownership");

  /** Log lines are capped so a chatty `compose up` cannot grow without bound. */
  const MAX_LINES = 400;
  let lines = [];

  /**
   * The highest `seq` already drawn.
   *
   * The page replays the history on paint *and* subscribes to the live stream,
   * so without this every event between the two would be drawn twice — and on
   * the failure that matters most, "Docker is not installed" appearing twice
   * reads as two different problems. See `Progress.seq`.
   */
  let drawn = -1;

  function append(line) {
    lines.push(line);
    if (lines.length > MAX_LINES) {
      lines = lines.slice(lines.length - MAX_LINES);
    }
    log.textContent = lines.join("\n");
    log.scrollTop = log.scrollHeight;
  }

  function showFailure(problem) {
    if (problem === undefined) {
      return;
    }
    failureTitle.textContent = problem.title;
    failureDetail.textContent = problem.detail;
    failureRemedy.textContent = problem.remedy;
    failure.hidden = false;
    headline.textContent = problem.title;
    detail.textContent = "The app did not start. Nothing was left running.";
  }

  function render(progress) {
    if (typeof progress.seq === "number") {
      if (progress.seq <= drawn) {
        return;
      }
      drawn = progress.seq;
    }

    if (progress.kind === "log") {
      append(progress.note);
      return;
    }

    if (progress.kind === "checking-docker") {
      headline.textContent = progress.note;
      detail.textContent = "";
      append(progress.note);
      return;
    }

    if (progress.kind === "docker-missing" || progress.kind === "failed") {
      append(progress.note);
      showFailure(progress.problem);
      return;
    }

    headline.textContent = progress.note;
    detail.textContent = "";
    append(progress.note);

    if (progress.kind === "backend-already-running") {
      ownership.textContent =
        "This stack was already running. Quitting OpsPilot will leave it running.";
    } else if (progress.kind === "backend-ready" && progress.ownership === "started-by-us") {
      ownership.textContent = "OpsPilot started this stack. Quitting will stop it.";
    } else if (progress.kind === "teardown-skipped") {
      ownership.textContent = "";
    }
  }

  if (typeof window.opspilot === "undefined") {
    showFailure({
      title: "The shell did not connect",
      detail:
        "The bootstrap page loaded without its preload bridge, so it cannot report what the app " +
        "is doing. That means the preload script failed to load.",
      remedy: "Quit and start the app again. If it repeats, this is a bug in the shell.",
    });
    return;
  }

  window.opspilot.onProgress(render);
  window.opspilot
    .getState()
    .then(function (state) {
      // Replay rather than re-render only the tail: the first events — including
      // a missing Docker — can be emitted before this page had a listener.
      state.progress.forEach(render);
      if (state.dashboardUrl !== null) {
        window.location.assign(state.dashboardUrl);
      }
    })
    .catch(function (error) {
      showFailure({
        title: "The shell did not answer",
        detail: String(error),
        remedy: "Quit and start the app again. If it repeats, this is a bug in the shell.",
      });
    });
})();