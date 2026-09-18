import { ApiProperty } from '@nestjs/swagger';
import { Difficulty } from '@prisma/client';
import { Type } from 'class-transformer';
import { IsEnum, IsInt, IsString, Max, Min, MinLength } from 'class-validator';

export class CreateProblemDto {
  @ApiProperty()
  @IsString()
  @MinLength(3)
  title!: string;

  @ApiProperty()
  @IsString()
  description!: string;

  @ApiProperty({ enum: Difficulty })
  @IsEnum(Difficulty)
  difficulty!: Difficulty;

  @ApiProperty()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  points!: number;

  @ApiProperty()
  @Type(() => Number)
  @IsInt()
  @Min(100)
  timeLimitMs!: number;

  @ApiProperty()
  @Type(() => Number)
  @IsInt()
  @Min(16)
  @Max(1024)
  memoryLimitMb!: number;

  @ApiProperty()
  @IsString()
  inputFormat!: string;

  @ApiProperty()
  @IsString()
  outputFormat!: string;
}
