# Contributing to proxpi

Thanks for wanting to help `proxpi` out!

Please review the [code of conduct](./CODE_OF_CONDUCT.md) before participating.

## Development set-up

Requires [Python](https://www.python.org/) and a Python package installer (eg
[pip](https://pip.pypa.io)).

After cloning (and optionally setting up a
[virtual environment](https://docs.python.org/3/tutorial/venv.html)), install the
package and the testing dependencies:

```shell
python -m pip install . -r tests/requirements.txt
```

Replace `.` with `-e .` to install the project as [editable](
  https://pip.pypa.io/en/stable/topics/local-project-installs/#editable-installs
) (ie not requiring a reinstall every time you make a change).

## Running the test suite

Tests are running using [pytest](https://docs.pytest.org/) (installed above):

```shell
python -m pytest
```

## Styling

Code is linted with [Black](https://pypi.org/project/black/). It needs to be installed
first:

```shell
python -m pip install black
```

Then run and commit before submitting a pull-request:

```shell
python -m black src
```
