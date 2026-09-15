# Using the GRC flowgraph (05a) — one-time local setup

`05a_IQtoOgg_plot.grc` uses a **custom GRC block, `Plot Capture`**, that ships
in this repo under [`grc_blocks/`](grc_blocks/). GNU Radio Companion (GRC) does
**not** discover custom blocks automatically — you point it at that folder
once, on your own machine. This page is that setup, plus the Windows config
gotcha that makes it trickier than it looks.

> Companion in Traditional Chinese: [`GRC_SETUP.zh-TW.md`](GRC_SETUP.zh-TW.md).

---

## 1. Do you even need this?

| What you want to do | Need this setup? |
|---|---|
| **Run** the flowgraph — `python 05a_IQtoOgg_plot.py` | **No.** |
| **Open / edit** `05a_IQtoOgg_plot.grc` in `gnuradio-companion` (the GUI) | **Yes** — otherwise the canvas shows `Missing Block  key: plot_capture`. |

**Why the split.** These are two different mechanisms:

- **Run-time** (executing the generated `.py`): all that happens is Python does
  `import gr_plot_capture`. That is handled by the 2-line shim
  [`gr_plot_capture.py`](gr_plot_capture.py) sitting next to the flowgraph,
  which redirects the import into `grc_blocks/`. **No GRC setting is involved**,
  so running the script works on a fresh clone with zero configuration.
- **Design-time** (GRC drawing the block on its canvas): GRC builds its block
  *library* only from the folders listed in its `local_blocks_path` setting. If
  `grc_blocks/` isn't in that list, GRC has never heard of `plot_capture` and
  renders it as a red **Missing Block**.

So: **only people who want to edit the flowgraph in the GUI need this.** Anyone
who just runs the pipeline can skip the whole page.

---

## 2. Why it isn't automatic, and why `git` can't carry it

The block *definition* (`grc_blocks/plot_capture.block.yml`) **is** in the repo
and travels with `git`. What does **not** travel is the setting that tells GRC
to look there — `local_blocks_path`. It can't, for two reasons:

1. It lives in your **per-machine GNU Radio user config** (`config.conf` under
   your home / AppData), a place `git` never touches.
2. It's an **absolute path**, and everyone clones this repo to a different
   location, so there is no one value that could be committed.

That's why each person sets it **once, locally** — you're not fixing a repo
bug, you're telling *your* GRC where *your* copy of the folder is.

---

## 3. The setup (copy-paste)

**Step 1.** Open the **same shell you launch `gnuradio-companion` from** (on
Windows this is normally the *radioconda prompt* — this matters, see §4) and
`cd` into this folder:

```bash
cd <where-you-cloned>/05_GNURadio_Czechia
```

**Step 2.** Run the snippet below. It computes the absolute path to
`grc_blocks/` for you, **appends** it to whatever custom-block paths you already
have (so it won't clobber other OOT blocks), and drops any dead entries along
the way:

```python
import os
from gnuradio import gr

blocks = os.path.abspath('grc_blocks')
p = gr.prefs()
# keep existing, still-valid entries; drop dead ones and any duplicate of ours
kept = [x for x in p.get_string('grc', 'local_blocks_path', '').split(os.pathsep)
        if x and os.path.isdir(x) and os.path.normpath(x) != os.path.normpath(blocks)]
kept.append(blocks)
p.set_string('grc', 'local_blocks_path', os.pathsep.join(kept))
p.save()
print('local_blocks_path =', p.get_string('grc', 'local_blocks_path', ''))
```

As a one-liner you can paste straight into any shell (`cmd`, PowerShell, bash):

```bash
python -c "import os; from gnuradio import gr; b=os.path.abspath('grc_blocks'); p=gr.prefs(); k=[x for x in p.get_string('grc','local_blocks_path','').split(os.pathsep) if x and os.path.isdir(x) and os.path.normpath(x)!=os.path.normpath(b)]; k.append(b); p.set_string('grc','local_blocks_path', os.pathsep.join(k)); p.save(); print('local_blocks_path =', p.get_string('grc','local_blocks_path',''))"
```

(If `python` isn't the radioconda one on your `PATH`, use the full path, e.g.
`C:\Users\<you>\radioconda\python.exe`.)

**Step 3.** **Fully quit** `gnuradio-companion` if it's open — GRC reads
`local_blocks_path` **only at startup**, so a running instance will not pick up
the change, and *reopening the file inside it is not enough*. Then relaunch and
open `05a_IQtoOgg_plot.grc`. `Plot Capture` now appears instead of a Missing
Block.

**Verify at any time:**

```bash
python -c "from gnuradio import gr; print(gr.prefs().get_string('grc','local_blocks_path',''))"
```

You should see a path ending in `...05_GNURadio_Czechia\grc_blocks` (alongside
any other custom-block folders you had).

---

## 4. Windows gotcha: there are TWO config files, and `HOME` decides which one

This is the subtle part, and it is exactly what caused a `Missing Block` in this
project's own history. GNU Radio's C++ preferences layer picks the folder it
reads `config.conf` from based on the `HOME` environment variable:

| Launch environment | `HOME` | config file GNU Radio reads |
|---|---|---|
| Git-Bash / WSL | **set** | `C:\Users\<you>\.gnuradio\config.conf` |
| `cmd` / PowerShell / **radioconda prompt** | **unset** | `C:\Users\<you>\AppData\Roaming\.gnuradio\config.conf` |

These are **two different files.** The consequence:

- **The safe rule: run the Step 2 snippet from the *same shell you use to launch
  `gnuradio-companion`*.** Then you are guaranteed to write the same file GRC
  will read. For most people that's the radioconda prompt (the AppData copy).
- **If you launch GRC more than one way** (say, sometimes from radioconda and
  sometimes from Git-Bash), run Step 2 **once in each**, so both config files
  agree. To force the no-`HOME` (radioconda) case from a Bash shell for testing,
  prefix the command with `env -u HOME`.

There is also a decoy file `C:\Users\<you>\.gnuradio\grc.conf` (note: `grc.conf`,
not `config.conf`). It holds only GUI window state (recent files, panel sizes) —
**it is not where `local_blocks_path` lives**; don't edit it.

> Do **not** hand-edit `config.conf`. Always go through
> `gr.prefs().set_string(...).save()` as in Step 2 — that writes to whichever
> file GNU Radio itself resolves for your current shell, which is the only way
> `gnuradio-companion` and `grcc` are guaranteed to agree.

---

## 5. Still `Missing Block  key: plot_capture`? Checklist

1. **Did you fully restart GRC?** Not just reopen the `.grc` — quit the whole
   application (confirm no `gnuradio-companion` process remains) and relaunch.
2. **Did you set it in the right config file?** Re-run the *verify* command from
   §3 **in the shell you launch GRC from** (§4). If it doesn't list
   `...05_GNURadio_Czechia\grc_blocks`, you set the other file — re-run Step 2
   from that same shell.
3. **Does the folder path actually exist and contain the block?** Confirm
   `grc_blocks/plot_capture.block.yml` is present at the path you registered
   (a moved/renamed clone is the classic cause).
4. **Sanity-check headlessly with `grcc`**, which uses the same block loader as
   the GUI and prints its search paths:
   ```bash
   grcc -o . 05a_IQtoOgg_plot.grc
   ```
   If `grcc` lists `...05_GNURadio_Czechia\grc_blocks` under "Block paths" and
   exits 0, the block is registered correctly and the problem is a stale GUI
   instance (go back to step 1).
