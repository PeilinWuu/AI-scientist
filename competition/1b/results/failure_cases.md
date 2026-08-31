# Failure Cases

- **missing_file**: `rejected` — input file does not exist: input/not_found.csv → `correct_input`
- **invalid_operation**: `rejected` — 1 validation error for ExecutionRequest
operation
  Input should be 'inspect_dataset', 'describe_dataset', 'missingness', 'correlation', 'linear_regression', 'plot_histogram', 'plot_scatter' or 'run_simulation' [type=literal_error, input_value='run_arbitrary_code', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/literal_error → `correct_input`
- **parameter_out_of_range**: `rejected` — damping must be between 0.001 and 5 → `correct_input`
- **path_escape**: `rejected` — path escapes the project boundary → `human_review`
