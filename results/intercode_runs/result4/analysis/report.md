# InterCode Permission Analysis

## Headline

- ROOT: attempted `200`, exact success `38.5%`, average reward `0.792`.
- STRICT: attempted `200`, exact success `29.0%`, average reward `0.735`.

## Plot Guide

- `exact_success_by_profile.svg`: strict한 exact match 기준에서 어느 권한이 더 많이 통과하는지 보여준다.
- `average_reward_by_profile.svg`: 부분점수까지 포함한 전체 성능 차이를 보여준다.
- `split_exact_success.svg`: split별로 어느 권한에서 성능 차이가 커지는지 보여준다.
- `reward_delta_histogram.svg`: task별 `ROOT - STRICT` reward 차이 분포를 보여준다.
- `failure_buckets_by_profile.svg`: near-miss, permission denied, semantic miss 같은 실패 유형 구성을 보여준다.

## Interpretation Notes

- exact success는 보수적인 지표라 near-miss를 많이 놓칠 수 있다.
- average reward는 InterCode 원래 채점 기준을 따르므로 출력 mismatch와 state mismatch가 함께 반영된다.
- permission denied 비중이 높으면 strict 제약이 실제로 utility를 줄였다고 해석할 수 있다.
