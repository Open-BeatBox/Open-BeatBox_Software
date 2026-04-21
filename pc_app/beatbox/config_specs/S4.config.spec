# configspec for the main section
n_trials = integer(min=1, default=4)
inter_trial_interval = integer(min=0, default=2000)

[S4]
corrective_loop_timeout = integer(min=0, default=3000)
error_flash_ms = integer(min=0, default=2000)
learning_threshold = float(min=0.0, max=1, default=0.8)
correct_pattern = string(default='w')
incorrect_pattern = string(default='b')
screen_sequence = list_w_choice('l', 'r')
advanced = boolean(default=True)
time_pressure_ms = integer(min=0, default=15000)
discovery_pattern = string(default='b')
left_screen_sequence = list_w_choice('w', 'b', 'v', 'h', 'd', 'g', 'trbl', 'tlbr')
right_screen_sequence = list_w_choice('w', 'b', 'v', 'h', 'd', 'g', 'trbl', 'tlbr')
