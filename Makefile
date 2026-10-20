CC      := gcc
CFLAGS  := -Wall -g -Iinclude
SRCS    := $(wildcard src/*.c)
OBJS    := $(SRCS:.c=.o)
BINARY  := my_shell

all: $(BINARY)

$(BINARY): $(OBJS)
	$(CC) $(CFLAGS) $^ -o $@

src/%.o: src/%.c include/shell.h
	$(CC) $(CFLAGS) -c $< -o $@

.PHONY: clean
clean:
	rm -f $(OBJS) $(BINARY)
