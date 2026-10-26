objects=main.o
program=xor_cipher

.PHONY:
all:$(program) $(objects)
$(program):$(objects)
	cc -o $(program) $(objects)

$(objects):%.o:%.c
	cc -c $< -o $@

install:
	./$(program)

clean:
	rm -rf *.o $(program) libboost.so

