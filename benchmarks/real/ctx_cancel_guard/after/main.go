package svc

import "context"

func start(ctx context.Context) {
	workCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	go doWork(workCtx)
}
