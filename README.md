    # SigmaOS-translations

    Translation catalogue manager

    Part of the **SigmaOS** sovereign operating-system ecosystem — a multi-repository
    distribution modelled on Linux Mint and Omarchy Linux. See the parent
    [`SigmaOS`](https://github.com/AaryanSinghChauhan09/SigmaOS) repository for the
    full component index.

    ## Upstream inspirations
    - https://github.com/linuxmint/cinnamon-translations

    ## Design principles applied
    - **Abstraction**
- **Separation of Concerns**
- **Defensive Programming**
- **Fail-Fast**
- **KISS**
- **DRY**
- **Deterministic Testing**

    ## Usage
    Verbs: `catalog, progress, update-plan, export`

    ```
    $ sigma-translations catalog ./po
de.po   120 entries  75.0% translated
fr.po   120 entries  92.5% translated
$ sigma-translations progress de.po
total=120 translated=90 fuzzy=10 untranslated=20 percent=75.0
$ sigma-translations update-plan messages.pot de.po
new      3   obsolete 5   untranslated 20
$ sigma-translations export ./po --out report.csv --format csv

    ```

    ## Build & test
    ```
    make build
    make test
    ```

    ## Licence
    GNU General Public License v3.0 (FOSS).
