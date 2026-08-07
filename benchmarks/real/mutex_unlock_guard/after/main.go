package store

import "sync"

type Store struct {
	mu   sync.Mutex
	data map[string]string
}

func (s *Store) Get(key string) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	v, ok := s.data[key]
	if !ok {
		return "", errNotFound
	}
	return v, nil
}
