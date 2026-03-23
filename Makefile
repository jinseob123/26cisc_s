# 자주 쓰는 실험 준비/실행/정리 명령을 make 타깃으로 묶어둔 파일.
.PHONY: up compose-up intercode-images down logs ps clean-intercode command_set

# docker compose 서비스와 InterCode 이미지를 한 번에 준비한다.
up: compose-up intercode-images

# split 1~4에 대한 command set을 다시 생성한다.
command_set:
	conda run -n intercode python agent/bench_intercode.py generate-set --split 1
	conda run -n intercode python agent/bench_intercode.py generate-set --split 2
	conda run -n intercode python agent/bench_intercode.py generate-set --split 3
	conda run -n intercode python agent/bench_intercode.py generate-set --split 4

	
# 루트의 docker-compose.yml 기준으로 서비스들을 빌드하고 백그라운드에서 실행한다.
compose-up:
	docker compose up --build -d

# InterCode split별 Docker 이미지(fs1~fs4)를 빌드한다.
intercode-images:
	cd bench/intercode && bash setup.sh

# docker compose 서비스와 InterCode 이미지를 한 번에 준비한다.
up: compose-up intercode-images

# root 실행과 strict 실행을 순차적으로 모두 수행한다.
run-all: run-root run-strict

# 생성된 모든 command set을 root 권한 프로파일로 순차 실행한다.
run-root:
	for d in /home/tako/jinseob/2026sisc_ss/results/intercode_command_sets/*; do \
		[ -f "$$d/commands.jsonl" ] || continue; \
		conda run -n intercode python agent/bench_intercode.py run-set \
			--records-path "$$d/commands.jsonl" \
			--profile root; \
	done



# 생성된 모든 command set을 strict 권한 프로파일로 순차 실행한다.
run-strict:
	for d in /home/tako/jinseob/2026sisc_ss/results/intercode_command_sets/*; do \
		[ -f "$$d/commands.jsonl" ] || continue; \
		conda run -n intercode python agent/bench_intercode.py run-set \
			--records-path "$$d/commands.jsonl" \
			--profile strict; \
	done


# docker compose로 띄운 서비스들을 종료한다.
down:
	docker compose down

# docker compose 서비스 로그를 실시간으로 확인한다.
logs:
	docker compose logs -f

# docker compose 서비스 상태와 InterCode 관련 컨테이너 상태를 확인한다.
ps:
	docker compose ps
	docker ps -a | grep intercode-nl2bash || true

# InterCode 실험용 컨테이너들을 강제로 삭제해 깨끗한 상태로 만든다.
clean-intercode:
	docker rm -f intercode-nl2bash_ic_ctr intercode-nl2bash_ic_ctr_eval 2>/dev/null || true
	docker rm -f intercode-nl2bash-fs1_ic_ctr intercode-nl2bash-fs1_ic_ctr_eval 2>/dev/null || true
	docker rm -f intercode-nl2bash-fs2_ic_ctr intercode-nl2bash-fs2_ic_ctr_eval 2>/dev/null || true
	docker rm -f intercode-nl2bash-fs3_ic_ctr intercode-nl2bash-fs3_ic_ctr_eval 2>/dev/null || true
	docker rm -f intercode-nl2bash-fs4_ic_ctr intercode-nl2bash-fs4_ic_ctr_eval 2>/dev/null || true

# docker compose 서비스 종료와 InterCode 컨테이너 정리를 함께 수행한다.
clean: down clean-intercode
