/**
 * The Windows icon the packaged executable actually carries.
 *
 * ## The gap this closes
 *
 * `desktop/scripts/make-assets.py --check` runs in the `lint` job, and it is a
 * real guard: it regenerates `desktop/assets/icon.ico` from
 * `docs/assets/icon-source.png` into a temporary directory and compares the
 * two picture against picture -- the decoded pixels, not the bytes. An
 * `.ico` holds PNG frames, and what a PNG encoder writes for an image
 * depends on the compressor, so an icon's *bytes* depend on the Pillow and
 * the zlib that packed them while its *pixels* do not. So the repository
 * knows that the *icon file* is the artwork.
 *
 * What the repository does not know is that the *icon file* is in the
 * executable. `desktop/electron-builder.yml` says `win.icon: assets/icon.ico`,
 * and `electron-builder` hands that file to a resource editor which writes
 * the icon into the PE resources of `OpsPilot.exe`. Every link in that chain
 * can break -- a path that stops resolving, a packager that changes how it
 * embeds icons, a resource editor that quietly drops a frame -- and the
 * symptom is always the same, and it is always the user's: they see the old
 * icon.
 *
 * Nothing in the tree would notice. The artefact is gitignored
 * (`desktop/.gitignore`), so it is not in a diff, and the icon is a binary,
 * so a reviewer cannot see it either. The only check that could catch this is
 * a check that reads the built executable -- and there was none.
 *
 * ## What this asserts
 *
 * Four things, in increasing order of what they would catch:
 *
 *   1. The committed `.ico` carries the ten sizes the artwork is resampled
 *      to.
 *   2. The built `OpsPilot.exe` carries a group icon that lists exactly
 *      those ten sizes.
 *   3. Every one of those ten is backed by a real icon resource in the same
 *      file, holding the committed icon's bytes.
 *   4. Windows itself -- asked for an icon out of the executable the way
 *      Explorer asks -- hands back the committed icon's 32-pixel frame, pixel
 *      for pixel.
 *
 * The second is the headline: it is the "did the ten sizes get in" question,
 * and it is the one that fails first when `electron-builder` stops embedding
 * the icon.
 *
 * The third is what makes the guard worth having. Sizes alone would not
 * catch a *stale* icon: an older artwork that also carried ten sizes would
 * pass every size check, and the user would still be looking at the old
 * badge. The bytes are what the user actually sees, so the bytes are what
 * gets compared.
 *
 * The fourth is the only evidence in this file that comes from the operating
 * system rather than from a parser in this repository. It is what notices an
 * icon resource that is present, correct in its directory, and unreadable.
 *
 * ## Why bytes here, and pictures there
 *
 * The two checks in this repository compare different things, and that is
 * why they compare differently. `make-assets.py --check` asks whether the
 * icon in the tree is still the icon the artwork makes; the answer has to
 * survive a change of Pillow or of zlib, so it compares the decoded
 * pictures. This check asks whether the executable carries the icon the
 * tree has; the icon resource inside the executable is this file's bytes
 * and nothing else -- the resource editor copies them across unchanged --
 * so both sides are the same committed bytes, nothing has to be decoded or
 * re-encoded, and the bytes are both the cheaper and the stricter
 * comparison. It is also the one that catches the symptom this check
 * exists for: an icon that is the right size and the wrong picture.
 *
 * The sizes are a contract, not a decoration. `make-assets.py` has
 * `SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)` and resamples the
 * artwork to each of them, because Windows asks for an icon by what it is
 * drawing rather than by asking, and scales up whatever is missing. A
 * comparison of the size set and the bytes needs no PNG decoder in a tree
 * that deliberately has no production dependency, and the sizes are the
 * ones the repository already promises.
 *
 * ## How the executable is read
 *
 * In Node, with nothing installed. `desktop/package.json` has no production
 * dependency and deliberately so -- `electron-builder.yml` says why -- and a
 * checker that needed a library to read a file format would mean either
 * adding one or installing it in CI for this job alone. The PE format is
 * documented, and the part of it that matters here is small: the resource
 * directory is a three-level tree (type, then name, then language), and a
 * group icon's data is a list of (width, height, icon id) triples.
 *
 * The reading is deliberately conservative. A file that is not a PE file, a
 * PE file with no resource directory, and a PE file with no group icon are
 * three different errors, because "this executable has no icon" and "this
 * file is not an executable" are different failures, and whoever reads the
 * failure needs to know which one they are looking at.
 *
 * ## Why Windows is asked as well
 *
 * Because a resource directory can be perfectly well formed and still not
 * render. `ExtractAssociatedIcon` is the call the shell makes, and it is the
 * only assertion here that is the operating system's opinion rather than
 * this repository's.
 *
 * It is also the assertion with the sharpest edge. GDI+ cannot render a
 * PNG-compressed icon frame -- handing `System.Drawing.Icon` a `.ico` whose
 * frames are PNGs produces a *different picture* from the same file, which
 * is exactly the kind of false negative a guard must not have. So the
 * committed icon's frame is written out as a plain PNG and read back as one,
 * and only the executable is read through the icon API.
 *
 * ## Why it skips rather than fails when there is no package
 *
 * The same rule as `packaged-posture.spec.ts`, for the same reason:
 * `desktop/release/` is gitignored and built by `npm run package`, so a
 * fresh clone has no executable to read. A skip that names what is missing
 * is the honest answer on a developer's machine, and the `desktop-package`
 * job in `.github/workflows/ci.yml` sets `OPSPILOT_PACKAGED_REQUIRED=1` so
 * that the same skip there is a failure -- the job built the package one
 * step earlier, so a skip means the build did not produce what was asked
 * for.
 *
 * ## Why the name carries "packaged-posture"
 *
 * The CI job selects this spec by the filter `packaged-posture`, which
 * matches the file path. A name without it would leave this check running
 * only in the `desktop-check` job on Linux, where it skips -- a green run
 * that proves nothing. And if the file were renamed, the job's own count
 * assertion (six checks, not five) would fail, which is the second line of
 * defence behind the name.
 */

import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const DESKTOP_DIR = path.resolve(__dirname, "..");

/** The committed multi-size icon the packaging configuration points at. */
const COMMITTED_ICON = path.join(DESKTOP_DIR, "assets", "icon.ico");

/** The unpacked executable the installer is built from. */
const PACKAGED_EXE = path.join(DESKTOP_DIR, "release", "win-unpacked", "OpsPilot.exe");

/**
 * The sizes the artwork is resampled to, and the sizes the executable must
 * therefore carry.
 *
 * These are the same ten numbers as `make-assets.py`'s `SIZES`, written out
 * rather than imported: that script is Python, this is TypeScript, and the
 * two are held together by this file's docstring and by the `lint` job's
 * `--check` rather than by a shared constant. Writing them down here is what
 * makes the check decisive -- an executable carrying nine of them fails, and
 * one carrying an eleventh fails as well.
 */
const EXPECTED_ICON_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256];

/** The resource type a run of icon images is filed under. */
const RT_ICON = 3;

/** The resource type the list of icons is filed under. */
const RT_GROUP_ICON = 14;

/**
 * A section header: the map from a relative virtual address to a file
 * offset.
 */
interface PeSection {
  virtualAddress: number;
  virtualSize: number;
  sizeOfRawData: number;
  pointerToRawData: number;
}

/** One entry of a directory in the PE resource tree. */
interface ResourceDirectoryEntry {
  /** The numeric id this entry is filed under. */
  id: number;
  /** The offset of the subdirectory or data entry, relative to the resource section. */
  offset: number;
}

/** A resource found in the PE resource directory, with the bytes it holds. */
interface PeResource {
  /** The id this resource is filed under, within its type. */
  id: number;
  /** The language it was filed under. */
  language: number;
  /** The bytes of the resource itself. */
  data: Buffer;
}

/** One frame of a group icon: the size it is drawn at, and the icon behind it. */
interface IconFrame {
  /** The width and height of the frame, in pixels. */
  size: number;
  /** The id of the `RT_ICON` resource that holds the frame's image. */
  iconId: number;
}

/** One frame of a `.ico` file: its size and its bytes. */
interface IcoFrame {
  /** The width and height of the frame, in pixels. */
  size: number;
  /** The image data, which is a PNG in the committed icon. */
  data: Buffer;
}

/** What Windows makes of the icon in an executable. */
interface WindowsIconView {
  /** Did Windows find an icon at all? */
  extracted: boolean;
  /** The width of the icon Windows handed back. */
  width: number;
  /** The height of the icon Windows handed back. */
  height: number;
  /** Were the two images the same size, so that they could be compared at all? */
  comparable: boolean;
  /** How many pixels differ from the committed icon's frame. */
  differing: number;
  /** How many pixels are not fully transparent. */
  opaque: number;
}

/**
 * Is the packaged artefact *required* rather than optional?
 *
 * The same switch `packaged-posture.spec.ts` reads, and for the same reason:
 * on a developer's machine `desktop/release/` does not exist, and a skip
 * that names why is the honest answer. In the `desktop-package` CI job it
 * does exist -- the job built it -- and a skip there would be a green run
 * that proved nothing.
 */
const REQUIRED = process.env.OPSPILOT_PACKAGED_REQUIRED === "1";

/**
 * Refuse to continue: a skip on a machine with no package, a failure in CI.
 *
 * Copied rather than imported from `packaged-posture.spec.ts`, and the copy
 * is deliberate: importing one spec file from another couples two files'
 * test registration for the sake of fifteen lines, and a reader of either
 * file then has to know that the other one exists.
 */
function requireArtefact(reason: string): void {
  if (REQUIRED) {
    throw new Error(
      `${reason}\n\nThis run was started with OPSPILOT_PACKAGED_REQUIRED=1, so a ` +
        "missing artefact is a failure and not a skip. The job that runs this " +
        "spec builds the package first, so a skip here means the build did not " +
        "produce what was asked for -- which is exactly the failure a green " +
        "skipped run would hide.",
    );
  }
  test.skip(true, reason);
}

/**
 * The packaged executable, or `null` with a recorded reason.
 *
 * Unlike the stack in `packaged-posture.spec.ts`, this does not refuse a
 * platform: reading a PE file is not a Windows-only operation, and refusing
 * to look would mean the check never ran anywhere but the Windows job --
 * which is where it runs anyway, because that is the only place the
 * executable exists.
 */
function packagedExeOrSkip(): string | null {
  if (!existsSync(PACKAGED_EXE)) {
    requireArtefact(
      `no packaged executable to inspect at ${PACKAGED_EXE}; run \`npm run package:dir\` ` +
        "in desktop/ to build one. Its absence is why this check is a skip and not a " +
        "failure -- release/ is gitignored and reproducible (desktop/.gitignore).",
    );
    return null;
  }
  return PACKAGED_EXE;
}

/**
 * The section headers of a PE file, and where its resource directory is.
 *
 * A PE file's addresses are relative: the resource directory is named by a
 * *relative virtual address*, which is a position in the image as the loader
 * would map it, not a position in the file. Turning one into the other is
 * this function's whole job, and everything else in the reader is useless
 * without it.
 *
 * The section headers and the resource table's address are read together
 * because they sit next to each other in the header, and because a reader
 * that had to ask for them separately would have to know that the resource
 * table is a *data directory entry* in the optional header rather than
 * something the section table points at.
 */
function readPeHeader(buffer: Buffer): { sections: PeSection[]; resourceRva: number } {
  // The DOS header is the first 64 bytes of every PE file, and the second
  // one ends with `e_lfanew`: the offset of the PE header itself.
  if (buffer.readUInt16LE(0) !== 0x5a4d) {
    throw new Error("not a PE file: the MZ signature is missing");
  }
  const peOffset = buffer.readUInt32LE(0x3c);
  if (peOffset + 24 > buffer.length) {
    throw new Error("not a PE file: the PE header offset is outside the file");
  }
  if (buffer.readUInt32LE(peOffset) !== 0x00004550) {
    throw new Error("not a PE file: the PE signature is missing");
  }

  // The COFF header follows the four-byte "PE\0\0" signature.
  const numberOfSections = buffer.readUInt16LE(peOffset + 6);
  const sizeOfOptionalHeader = buffer.readUInt16LE(peOffset + 20);
  const optionalHeader = peOffset + 24;
  const magic = buffer.readUInt16LE(optionalHeader);
  // The data directories sit at a different offset in a PE32 (0x10b) and a
  // PE32+ (0x20b) optional header, and the resource table is the third of
  // them.
  const dataDirectories = optionalHeader + (magic === 0x20b ? 112 : 96);
  const resourceRva = buffer.readUInt32LE(dataDirectories + 2 * 8);

  const sectionTable = optionalHeader + sizeOfOptionalHeader;
  const sections: PeSection[] = [];
  for (let index = 0; index < numberOfSections; index++) {
    const header = sectionTable + index * 40;
    sections.push({
      virtualSize: buffer.readUInt32LE(header + 8),
      virtualAddress: buffer.readUInt32LE(header + 12),
      sizeOfRawData: buffer.readUInt32LE(header + 16),
      pointerToRawData: buffer.readUInt32LE(header + 20),
    });
  }
  return { sections, resourceRva };
}

/**
 * The file offset a relative virtual address names.
 *
 * The span a section covers is the larger of its virtual size and its raw
 * size: a section can be larger in memory than on disk, and the address
 * that falls in the difference belongs to the section even though nothing
 * is there.
 */
function fileOffsetOf(sections: PeSection[], rva: number): number {
  for (const section of sections) {
    const span = Math.max(section.virtualSize, section.sizeOfRawData);
    if (rva >= section.virtualAddress && rva < section.virtualAddress + span) {
      return section.pointerToRawData + (rva - section.virtualAddress);
    }
  }
  throw new Error(`the address 0x${rva.toString(16)} is in no section`);
}

/**
 * The entries of one directory of the PE resource tree.
 *
 * A resource directory is a header of sixteen bytes followed by eight-byte
 * entries. The first half of an entry names the entry -- a number, or, when
 * the top bit is set, a string elsewhere in the section -- and the second
 * half says whether the entry is a further directory or the resource data
 * itself.
 *
 * Named entries are skipped. The types this file looks for are all numeric,
 * and a named entry in the same directory would otherwise be read as a type
 * id it is not.
 */
function readResourceDirectory(buffer: Buffer, directoryOffset: number): ResourceDirectoryEntry[] {
  const named = buffer.readUInt16LE(directoryOffset + 12);
  const identified = buffer.readUInt16LE(directoryOffset + 14);
  const entries: ResourceDirectoryEntry[] = [];
  for (let index = 0; index < named + identified; index++) {
    const entry = directoryOffset + 16 + index * 8;
    const nameOrId = buffer.readUInt32LE(entry);
    if ((nameOrId & 0x80000000) !== 0) {
      continue;
    }
    entries.push({
      id: nameOrId & 0x7fffffff,
      offset: buffer.readUInt32LE(entry + 4) & 0x7fffffff,
    });
  }
  return entries;
}

/**
 * Every resource of one type, with the bytes it holds.
 *
 * The tree is three levels deep -- type, then name, then language -- and
 * the leaves are data entries that point at the resource's bytes by
 * relative virtual address.
 */
function readResourcesOfType(
  buffer: Buffer,
  sections: PeSection[],
  resourceRva: number,
  typeId: number,
): PeResource[] {
  if (resourceRva === 0) {
    return [];
  }
  const resourceBase = fileOffsetOf(sections, resourceRva);
  const types = readResourceDirectory(buffer, resourceBase);
  const type = types.find((entry) => entry.id === typeId);
  if (type === undefined) {
    return [];
  }

  const resources: PeResource[] = [];
  for (const name of readResourceDirectory(buffer, resourceBase + type.offset)) {
    for (const language of readResourceDirectory(buffer, resourceBase + name.offset)) {
      const dataEntry = resourceBase + language.offset;
      const dataRva = buffer.readUInt32LE(dataEntry);
      const dataSize = buffer.readUInt32LE(dataEntry + 4);
      const offset = fileOffsetOf(sections, dataRva);
      resources.push({
        id: name.id,
        language: language.id,
        data: buffer.subarray(offset, offset + dataSize),
      });
    }
  }
  return resources;
}

/**
 * The size of an icon frame, from the two bytes that hold it.
 *
 * A zero means 256, because 256 does not fit in one byte. The width and the
 * height have to agree: the width is what the size is taken from, and a
 * frame that is not square would make that a lie.
 */
function readIconSize(width: number, height: number, what: string): number {
  const readWidth = width === 0 ? 256 : width;
  const readHeight = height === 0 ? 256 : height;
  if (readWidth !== readHeight) {
    throw new Error(`${what} lists a frame of ${readWidth}x${readHeight}`);
  }
  return readWidth;
}

/**
 * The frames a group icon lists.
 *
 * The data is a header of three 16-bit numbers -- reserved, type, count --
 * followed by one fourteen-byte record per frame: the width and height as
 * single bytes, then the planes, the bit count, the size of the image and
 * the id of the icon resource that holds it.
 */
function readGroupIconFrames(data: Buffer): IconFrame[] {
  const count = data.readUInt16LE(4);
  const frames: IconFrame[] = [];
  for (let index = 0; index < count; index++) {
    const record = 6 + index * 14;
    frames.push({
      size: readIconSize(data.readUInt8(record), data.readUInt8(record + 1), "the group icon"),
      iconId: data.readUInt16LE(record + 12),
    });
  }
  return frames;
}

/**
 * The frames a `.ico` file holds.
 *
 * The directory is a six-byte header followed by sixteen-byte records: the
 * width and the height as single bytes, the colour count, the reserved
 * field, the planes, the bit count, the size of the image and where the
 * image is.
 */
function readIcoFrames(buffer: Buffer): IcoFrame[] {
  const count = buffer.readUInt16LE(4);
  const frames: IcoFrame[] = [];
  for (let index = 0; index < count; index++) {
    const record = 6 + index * 16;
    const size = buffer.readUInt32LE(record + 8);
    const offset = buffer.readUInt32LE(record + 12);
    frames.push({
      size: readIconSize(buffer.readUInt8(record), buffer.readUInt8(record + 1), "the icon file"),
      data: buffer.subarray(offset, offset + size),
    });
  }
  return frames;
}

/**
 * The group icon an executable carries.
 *
 * Throws rather than returning an empty list when there is none, because
 * "this executable has no icon" and "this executable has an icon with no
 * frames" are the same failure to a user -- a window with the default
 * Electron icon -- and must not be confused with a successful reading.
 */
function readExecutableIconFrames(buffer: Buffer): IconFrame[] {
  const { sections, resourceRva } = readPeHeader(buffer);
  if (resourceRva === 0) {
    throw new Error("the executable has no resource directory");
  }
  const groups = readResourcesOfType(buffer, sections, resourceRva, RT_GROUP_ICON);
  if (groups.length === 0) {
    throw new Error("the executable carries no group icon");
  }
  return groups.flatMap((group) => readGroupIconFrames(group.data));
}

/**
 * What is wrong with a set of icon sizes, in words.
 *
 * The expected set is a parameter, not a constant, so that the check can be
 * asked about any set -- which is how the self-proof below asks it about a
 * set the artefact does not have.
 */
function iconSizeMismatches(actual: number[], expected: number[]): string[] {
  const problems: string[] = [];
  const present = new Set(actual);
  const missing = expected.filter((size) => !present.has(size));
  if (missing.length > 0) {
    problems.push(`missing sizes: ${missing.join(", ")}`);
  }
  const wanted = new Set(expected);
  const unexpected = [...new Set(actual)].filter((size) => !wanted.has(size));
  if (unexpected.length > 0) {
    problems.push(`unexpected sizes: ${unexpected.join(", ")}`);
  }
  return problems;
}

/**
 * Assert that a set of icon sizes is the one that was asked for.
 *
 * Throwing rather than returning a verdict, so that the check and the
 * self-proof below are the same code: the self-proof asks for a set the
 * artefact does not have and insists that this throws.
 */
function assertIconSizes(actual: number[], expected: number[]): void {
  const problems = iconSizeMismatches(actual, expected);
  if (problems.length > 0) {
    throw new Error(
      `the icon sizes are not the ${expected.join(", ")} the artwork is resampled to:\n  ` +
        problems.join("\n  "),
    );
  }
}

/**
 * The PowerShell that asks Windows what it makes of an executable's icon.
 *
 * `ExtractAssociatedIcon` is the call the shell makes, and the bitmap it
 * returns is what a user would see in Explorer's icon view. The comparison
 * is against a plain PNG file rather than against the `.ico` itself, because
 * GDI+ cannot render a PNG-compressed icon frame and would report a
 * difference that is not one.
 */
const POWERSHELL_ICON_VIEW = `param([string]$Exe, [string]$Frame)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
$icon = [System.Drawing.Icon]::ExtractAssociatedIcon($Exe)
if ($null -eq $icon) {
  Write-Output '{"extracted":false,"width":0,"height":0,"comparable":false,"differing":0,"opaque":0}'
  exit 0
}
$fromExe = $icon.ToBitmap()
$fromIco = New-Object System.Drawing.Bitmap($Frame)
$differing = 0
$opaque = 0
$comparable = $fromExe.Width -eq $fromIco.Width -and $fromExe.Height -eq $fromIco.Height
if ($comparable) {
  for ($y = 0; $y -lt $fromExe.Height; $y++) {
    for ($x = 0; $x -lt $fromExe.Width; $x++) {
      $a = $fromExe.GetPixel($x, $y)
      if ($a.A -gt 0) { $opaque = $opaque + 1 }
      if ($a.ToArgb() -ne $fromIco.GetPixel($x, $y).ToArgb()) { $differing = $differing + 1 }
    }
  }
}
$answer = @{ extracted = $true; width = $fromExe.Width; height = $fromExe.Height; comparable = $comparable; differing = $differing; opaque = $opaque }
Write-Output (ConvertTo-Json -Compress -InputObject $answer)
`;

/**
 * A number out of the answer Windows gave, or a failure that says what it
 * said instead.
 */
function readNumber(answer: Record<string, unknown>, key: string): number {
  const value = answer[key];
  if (typeof value !== "number") {
    throw new Error(`Windows' answer carried no number for ${key}: ${JSON.stringify(answer)}`);
  }
  return value;
}

/**
 * A boolean out of the answer Windows gave, or a failure that says what it
 * said instead.
 */
function readBoolean(answer: Record<string, unknown>, key: string): boolean {
  const value = answer[key];
  if (typeof value !== "boolean") {
    throw new Error(`Windows' answer carried no boolean for ${key}: ${JSON.stringify(answer)}`);
  }
  return value;
}

/**
 * What Windows makes of the icon in an executable.
 *
 * The PowerShell is written to a file rather than passed on a command line
 * because a script with quotes in it, run through a shell, is a quoting
 * bug waiting for a path with a space in it.
 */
function windowsIconView(exePath: string, framePath: string, scratch: string): WindowsIconView {
  const script = path.join(scratch, "icon-view.ps1");
  writeFileSync(script, POWERSHELL_ICON_VIEW, "utf8");
  let stdout: string;
  try {
    stdout = execFileSync(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, exePath, framePath],
      { encoding: "utf8" },
    );
  } catch (error) {
    throw new Error(
      `Windows could not be asked about the icon in ${exePath}: ${String(error)}\n` +
        "The check needs Windows PowerShell, which every Windows machine has; if this " +
        "is a Linux runner then the executable is not one to be read on this platform.",
    );
  }

  // Parsed as `unknown` and stepped through as a record rather than cast to
  // `any`: everything below reads this shape by key, and an untyped answer
  // would let a typo compile as a lookup that always misses.
  const parsed: unknown = JSON.parse(stdout.trim());
  if (typeof parsed !== "object" || parsed === null) {
    throw new Error(`Windows' answer did not parse into an object: ${stdout.trim()}`);
  }
  const answer = parsed as Record<string, unknown>;
  return {
    extracted: readBoolean(answer, "extracted"),
    width: readNumber(answer, "width"),
    height: readNumber(answer, "height"),
    comparable: readBoolean(answer, "comparable"),
    differing: readNumber(answer, "differing"),
    opaque: readNumber(answer, "opaque"),
  };
}

test.describe("the icon the package ships", () => {
  test("the packaged executable carries the ten icon sizes the artwork is resampled to", async () => {
    // ---------------------------------------------------------------------
    // The guard is not empty.
    //
    // A check that cannot fail is not a check, and a check that fails for
    // the wrong reason is worse than none. These three assertions run
    // before the artefact is even looked for, so they hold on every
    // machine -- including the Linux one, where the artefact does not
    // exist and the rest of this test skips.
    // ---------------------------------------------------------------------

    // The comparison must reject a set the artefact does not have. Without
    // this, a comparison that always returned "no problems" would pass
    // every real assertion in this file.
    await test.step("a size set the artefact does not have is reported", () => {
      expect(() => assertIconSizes([16, 32], EXPECTED_ICON_SIZES)).toThrow(/missing sizes/);
    });

    // And the comparison must accept the set that is expected -- otherwise
    // the assertion above would be satisfied by a comparison that rejects
    // everything.
    await test.step("the expected size set is accepted", () => {
      expect(() => assertIconSizes(EXPECTED_ICON_SIZES, EXPECTED_ICON_SIZES)).not.toThrow();
    });

    // The reader must refuse a file that is not an executable rather than
    // inventing a size set for it. The committed icon is the natural
    // non-executable: it is in the tree, and it is not a PE file.
    await test.step("a file that is not an executable is refused", () => {
      const committed = readFileSync(COMMITTED_ICON);
      expect(() => readExecutableIconFrames(committed)).toThrow(/not a PE file/);
    });

    // ---------------------------------------------------------------------
    // The committed icon.
    // ---------------------------------------------------------------------

    const committedFrames = readIcoFrames(readFileSync(COMMITTED_ICON));
    await test.step("the committed icon carries the ten sizes", () => {
      expect(() =>
        assertIconSizes(
          committedFrames.map((frame) => frame.size),
          EXPECTED_ICON_SIZES,
        ),
      ).not.toThrow();
    });

    // ---------------------------------------------------------------------
    // The artefact.
    // ---------------------------------------------------------------------

    const exe = packagedExeOrSkip();
    if (exe === null) {
      return;
    }

    const executable = readFileSync(exe);
    const frames = readExecutableIconFrames(executable);

    // The headline: the ten sizes are in the executable's group icon.
    await test.step("the executable's group icon lists the ten sizes", () => {
      expect(() =>
        assertIconSizes(
          frames.map((frame) => frame.size),
          EXPECTED_ICON_SIZES,
        ),
      ).not.toThrow();
    });

    // Each advertised size has to be backed by a real icon resource, and
    // that resource has to hold the committed icon's bytes -- otherwise the
    // group icon is a list of promises, and a stale artwork with the same
    // ten sizes would pass every check above.
    const icons = (() => {
      const { sections, resourceRva } = readPeHeader(executable);
      return readResourcesOfType(executable, sections, resourceRva, RT_ICON);
    })();

    await test.step("each of the ten is a real icon, and it is the committed icon's", () => {
      for (const frame of frames) {
        const resource = icons.find((candidate) => candidate.id === frame.iconId);
        if (resource === undefined) {
          throw new Error(
            `the group icon lists its ${frame.size}-pixel frame as icon ${frame.iconId}, and ` +
              "the executable has no resource with that id",
          );
        }
        const committed = committedFrames.find((candidate) => candidate.size === frame.size);
        if (committed === undefined) {
          throw new Error(
            `the group icon lists a ${frame.size}-pixel frame, and the committed icon has no ` +
              "frame of that size",
          );
        }
        if (!resource.data.equals(committed.data)) {
          throw new Error(
            `the executable's ${frame.size}-pixel icon is not the committed icon's ` +
              `${frame.size}-pixel frame: ${resource.data.length} bytes against ` +
              `${committed.data.length}. The executable carries a different picture at the ` +
              "size the user would see.",
          );
        }
      }
    });

    // ---------------------------------------------------------------------
    // The operating system's own view.
    // ---------------------------------------------------------------------

    if (process.platform !== "win32") {
      // Only reachable if an executable is somehow present on another
      // platform, which the package never produces. The rest of this file
      // is portable; this part is Windows' to answer.
      return;
    }

    const scratch = mkdtempSync(path.join(tmpdir(), "opspilot-icon-"));
    try {
      // The committed icon's 32-pixel frame, written out as a plain PNG.
      // GDI+ cannot render a PNG-compressed icon frame, so the frame is
      // handed to it as the PNG it is rather than as the icon it lives in.
      const frame32 = committedFrames.find((frame) => frame.size === 32);
      if (frame32 === undefined) {
        throw new Error("the committed icon has no 32-pixel frame to compare Windows' answer with");
      }
      const framePath = path.join(scratch, "frame-32.png");
      writeFileSync(framePath, frame32.data);

      const view = windowsIconView(exe, framePath, scratch);

      await test.step("Windows pulls the committed icon's 32-pixel frame out of the executable", () => {
        expect(view.extracted).toBe(true);
        expect({ width: view.width, height: view.height }).toEqual({ width: 32, height: 32 });
        // The two images have to be the same size before "not one pixel
        // differs" means anything. Without this, a comparison that could
        // not be made would report no differences and pass.
        expect(
          view.comparable,
          "the icon Windows pulled out of the executable is not the same size as the " +
            "committed icon's 32-pixel frame, so the two were never compared",
        ).toBe(true);
        // Not one pixel may differ: this is the picture the user sees.
        expect(
          view.differing,
          "the icon Windows pulled out of the executable is not the committed icon's " +
            "32-pixel frame",
        ).toBe(0);
        // And it is a picture, not an empty rectangle.
        expect(view.opaque).toBeGreaterThan(0);
      });
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });
});
