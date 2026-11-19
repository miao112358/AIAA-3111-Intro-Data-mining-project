# Member C case notes

## Popular but not strongly tidal

`utm18n_o0_v1:1000:585:4508<->utm18n_o0_v1:1000:585:4509` ranks first by observed weekday AM+PM volume with
91,391 rides. It has no representative frequent tidal
rule at Support 0.10, so it does not enter the tidal Top-10.

## Strongly tidal without being the busiest

`utm18n_o0_v1:1000:586:4510<->utm18n_o0_v1:1000:586:4512` is tidal rank 1 with Support
0.939, but it is only volume rank
47 with 30,823 rides. This is
the clearest example of why the popular and tidal rankings answer different
questions.

## Context dependence

For the tidal rank 1 corridor, weekday AM-to-PM Support is
0.939,
weekend AM-to-PM Support is
0.019,
and weekday AM-to-MID Support is
0.203.
The reversal is therefore concentrated in the weekday commute-window
comparison rather than being a general all-day or weekend property.

## Holdout behavior

The weakest same-orientation Holdout result is Development rank
2 with 47
joint days out of 66 and Support
0.712. It remains a successful
reproduction under the frozen 0.10 rule, while its lower value is retained as
evidence of temporal variation.
