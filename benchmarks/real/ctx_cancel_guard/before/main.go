package svc

import "context"

func start(ctx context.Context) {
	workCtx, _ := context.WithCancel(ctx)
	go doWork(workCtx)
}
