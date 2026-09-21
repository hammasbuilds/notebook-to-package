# Results

Two questions, two corpora, and the second corpus exists because the first one flattered
everybody.

**Can notebooks in the wild be run from clean?** Answered statically, on every notebook.

**Does the conversion preserve behaviour?** Answered by running the notebook and the
generated package and comparing what each left behind — on the subset that runs at all.

Reproduce with:

```bash
ntp survey <dir> --json survey.json
ntp bench  <dir> --python <env>/bin/python --timeout 60 --json bench.json
```

Raw output for every run is in this directory (`survey_*.json`, `bench_*.json`).

---

## The two corpora

| | what it is | source |
|---|---|---|
| **books** | notebooks published as books and courses, curated to be read in order | PythonDataScienceHandbook, pandas_exercises, cookbook-2nd-code, python-machine-learning-book-3rd-edition |
| **wild** | notebooks from ordinary repositories, written to get work done | ten repos from a GitHub search for recently-updated Jupyter projects |

The split matters. A book's notebooks are proofread; nobody proofreads an analysis notebook.
Measuring only the first would have produced a reassuring number about a population that is
not the one anybody has to deal with — the first partial run of the books corpus came back
**100%** clean, which is what prompted going and finding the second corpus.

---

## 1. Can they run from clean?

| | books | wild |
|---|---:|---:|
| notebooks found | 359 | 106 |
| excluded: unreadable JSON | 1 | 0 |
| excluded: no code cells (markdown-only chapters) | 67 | 0 |
| **analysed** | **291** | **106** |
| code cells | 4,611 | 3,264 |

Of those analysed:

| | books | wild |
|---|---:|---:|
| **provably run top to bottom in a fresh kernel** | 260 (**89%**) | 70 (**66%**) |
| use a name that is defined in no cell at all | 30 (10%) | 35 (**33%**) |
| use a name before the cell that defines it | 4 (1%) | 10 (9%) |
| were last run out of document order | 17 (6%) | 16 (15%) |
| contain a cell that was never run | 4 (1%) | 73 (**69%**) |
| contain IPython magics or shell escapes | 204 (70%) | 63 (59%) |
| star-import, so the question is undecidable | 8 (3%) | 23 (22%) |
| contain a cell that is not valid Python | 47 (16%) | 28 (26%) |

### Reading it

**A third of ordinary notebooks cannot be run from clean by anybody.** They use a name that
is assigned in no cell, imported in no cell, and is not a builtin. It came from a cell that
has since been deleted, or from a kernel somebody had been typing into for an hour.

**"Provably" is load-bearing.** A star-import binds names static analysis cannot see, so those
notebooks are counted in neither direction. 22% of the wild corpus star-imports, so 66% is a
floor, not an estimate.

**Cells that were never run: 1% against 69%.** A book ships with every cell executed. An
analysis notebook is full of half-written cells nobody got back to.

**The magics differ in kind, not just in count.**

| books | | wild | |
|---|---:|---|---:|
| `%matplotlib` | 169 | **`!shell`** | **318** |
| `%timeit` | 98 | `%pip` | 42 |
| `%load_ext` | 38 | `%matplotlib` | 28 |
| `%%writefile` | 27 | `%cd` | 23 |

Books use magics to *present*. Ordinary notebooks use them to install packages and move
around the filesystem — `!shell` and `%pip` together outnumber everything else, which is
also why so few of them run anywhere but the machine they were written on.

**16% and 26% of cells do not parse at all.** Reading them, these are pasted `In [1]:`
transcripts and smart quotes from copy-paste (`print('Prediction {}".format(...)`).

---

## 2. Does the conversion preserve behaviour?

```
books: 291 notebooks attempted, 1932s

    204  the notebook itself did not run here - excluded, nothing can be concluded
     87  both sides ran, so the conversion can be judged
     87  faithful (100.0%)
      0  broken

  22 notebooks produced 80 values that differ between two runs of the notebook itself
```

```
wild: 106 notebooks attempted, 196s

    102  the notebook itself did not run here
      4  both sides ran
      4  faithful - a sample far too small to quote
```

### Why the excluded notebooks did not run

| books | | wild | |
|---|---:|---|---:|
| ModuleNotFoundError | 107 | ModuleNotFoundError | 89 |
| OSError | 29 | FileNotFoundError | 7 |
| NameError | 20 | NameError | 2 |
| FileNotFoundError | 10 | ValueError | 1 |
| AttributeError | 9 | timed out | 1 |
| timed out (60s) | 6 | | |

This denominator is the honest part. Most notebooks want a package, a CSV, a GPU or a network
this machine does not have, and none of that says anything about the conversion. **On the wild
corpus only 4 of 106 were decidable**, which is why the fidelity figure rests on the books.

### The control run, and the four accusations it withdrew

Before it existed, this benchmark reported four conversion failures:

```
  pandas_exercises/05_Merge_Housing_Market_Exercises_with_solutions.ipynb
      differs: bigcolumn, housemkt, s1, s2, s3
  PythonDataScienceHandbook/notebooks_04.12-Three-Dimensional-Plotting.ipynb
      differs: xdata, ydata, zdata
```

Every one of them calls `np.random` with no seed. The values differ because **the notebook
differs from itself**, and the conversion had nothing to do with it. Four out of four false.

The fix is the same idea as a control arm: run the notebook a *second* time and discard every
name that differs between its own two runs. It costs a third execution per notebook and it is
the difference between a measurement and an accusation. On the books corpus it excluded 80
values across 22 notebooks.

### And one failure that was not a failure either

The first clean run reported 84/85, with this:

```
  pandas_exercises/07_Visualization_Chipotle_Exercise_with_Solutions.ipynb
      package raised: timed out after 25s
```

Re-run with a 180-second budget, that notebook is faithful. A timeout means *no verdict*, not
*broken* — so it is now classified as neither, and the budget was raised to 60s.

---

## 3. What these numbers do not say

- **Both corpora are convenience samples.** Four published books and ten repositories from one
  GitHub search. The books/wild gap is large and consistent enough to report; its exact size
  is not a population estimate.
- **100% fidelity is over 87 notebooks**, all of which ran in one environment with numpy,
  pandas, matplotlib, scipy and scikit-learn. Notebooks needing anything else are absent from
  the numerator *and* the denominator.
- **Comparison is by digest, not equality.** Two different objects with the same normalised
  `repr` compare equal here. That is weaker than equality, and weaker in a stated direction.
- **The static audit is syntactic.** `exec`, `globals()[...]` and star-imports are invisible;
  star-imports are detected and the notebook excused rather than accused.
- **Values that cannot be compared are excluded from both sides**, so a notebook whose entire
  output is opaque objects contributes a vacuous "faithful".

## Bugs these runs caught

| what it reported | what was true |
|---|---|
| four conversion failures | four notebooks using unseeded `np.random` — the notebook differs from itself |
| `math` is never defined, in a notebook that imports it | the cell also held `%matplotlib inline`, so the whole cell failed to parse and contributed no bindings |
| a helper reading a later global is "used before defined" | a function body is not evaluated until it is called |
| every notebook unfaithful, every name "only in the notebook" | `runpy.run_path` returns a copy of the globals taken *before* `main()` runs |
| one conversion "broken" | a 25-second timeout; faithful at 180 |
| 31 notebooks with unreadable JSON | my own truncated downloads over a throttled link, not a property of notebooks |
